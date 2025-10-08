from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import (
    CreateView,
    ListView,
    UpdateView,
    DetailView,
    DeleteView,
)
from reversion import revisions

from judge.forms import TutorialForm
from judge.models import Tutorial
from judge.utils.diggpaginator import DiggPaginator
from judge.utils.opengraph import generate_opengraph
from judge.utils.views import TitleMixin, generic_message


class TutorialMixin(object):
    model = Tutorial
    pk_url_kwarg = "id"
    slug_url_kwarg = "slug"

    def get_object(self, queryset=None):
        tutorial = super(TutorialMixin, self).get_object(queryset)
        if not tutorial.is_editable_by(self.request.user):
            raise PermissionDenied()
        return tutorial


class TutorialListBase(ListView):
    model = Tutorial
    paginate_by = 10
    context_object_name = "tutorials"
    title = None

    def get_paginator(
        self, queryset, per_page, orphans=0, allow_empty_first_page=True, **kwargs
    ):
        return DiggPaginator(
            queryset,
            per_page,
            body=6,
            padding=2,
            orphans=orphans,
            allow_empty_first_page=allow_empty_first_page,
            **kwargs,
        )

    def get_queryset(self):
        queryset = Tutorial.objects.filter(
            visible=True, publish_on__lte=timezone.now()
        ).prefetch_related("authors__user", "authors__display_badge")
        return queryset

    def get_context_data(self, **kwargs):
        context = super(TutorialListBase, self).get_context_data(**kwargs)
        context["first_page_href"] = None
        context["title"] = (
            self.title or _("Page %d of Tutorials") % context["page_obj"].number
        )
        return context


class TutorialList(TutorialListBase):
    template_name = "tutorial/list.html"

    def get_queryset(self):
        from django.db.models import Exists, OuterRef
        from judge.models.contest import Contest, ContestParticipation

        queryset = super(TutorialList, self).get_queryset()

        # Filter by organization if specified
        if self.request.user.is_authenticated and hasattr(self.request, "organization"):
            queryset = queryset.filter(organization=self.request.organization)
        else:
            queryset = queryset.filter(organization=None)

        user = self.request.user

        # For unauthenticated users, exclude all contest-restricted tutorials
        if not user.is_authenticated:
            # Exclude tutorials that are associated with any contests
            contest_restricted_tutorials = Contest.objects.filter(
                tutorial=OuterRef("pk")
            )
            queryset = queryset.exclude(Exists(contest_restricted_tutorials))
        else:
            # For authenticated users, only show contest-restricted tutorials they can access
            # Get contests where the user has participated
            user_participated_contests = ContestParticipation.objects.filter(
                user=user.profile, contest=OuterRef("tutorial")
            )

            # Get tutorials that are either:
            # 1. Not associated with any contests (public tutorials), or
            # 2. Associated with contests where the user has participated, or
            # 3. User is admin/author (handled by can_see method for edge cases)
            contest_restricted_tutorials = Contest.objects.filter(
                tutorial=OuterRef("pk")
            )

            # Exclude contest-restricted tutorials where user hasn't participated
            # unless they're admin/author (we'll handle that with individual can_see checks)
            inaccessible_tutorials = Contest.objects.filter(
                tutorial=OuterRef("pk")
            ).exclude(
                id__in=ContestParticipation.objects.filter(
                    user=user.profile
                ).values_list("contest_id", flat=True)
            )

            # Get tutorials that user can potentially see
            potentially_accessible = queryset.exclude(Exists(inaccessible_tutorials))

            # For remaining edge cases (admin/author access), do individual checks
            accessible_tutorials = []
            for tutorial in potentially_accessible:
                if tutorial.can_see(user):
                    accessible_tutorials.append(tutorial.pk)

            queryset = queryset.filter(pk__in=accessible_tutorials)

        return queryset.order_by("-publish_on")

    def get_context_data(self, **kwargs):
        context = super(TutorialList, self).get_context_data(**kwargs)
        context["first_page_href"] = reverse("tutorial_list")
        context["page_prefix"] = reverse("tutorial_list")

        # Add navigation links
        context["tutorial_list_link"] = reverse("tutorial_list")
        context["newsfeed_link"] = (
            f"{reverse('home')}?show_all_blogs=false&show_tutorials=false"
        )
        context["all_blogs_link"] = (
            f"{reverse('home')}?show_all_blogs=true&show_tutorials=false"
        )
        context["all_tutorials_link"] = (
            f"{reverse('home')}?show_tutorials=true&show_all_blogs=false"
        )

        return context


class TutorialView(TitleMixin, DetailView):
    model = Tutorial
    pk_url_kwarg = "id"
    context_object_name = "tutorial"
    template_name = "tutorial/content.html"

    def get_title(self):
        return self.object.title

    def get_context_data(self, **kwargs):
        context = super(TutorialView, self).get_context_data(**kwargs)

        metadata = generate_opengraph(
            "generated-meta-tutorial:%d" % self.object.id,
            self.object.summary or self.object.content,
            "tutorial",
        )
        context["meta_description"] = metadata[0]
        context["og_image"] = metadata[1]

        return context

    def get_object(self, queryset=None):
        tutorial = super(TutorialView, self).get_object(queryset)
        if not tutorial.can_see(self.request.user):
            raise Http404()
        return tutorial


class TutorialCreate(TitleMixin, CreateView):
    template_name = "tutorial/edit.html"
    model = Tutorial
    form_class = TutorialForm

    def get_title(self):
        return _("Creating new tutorial")

    def get_content_title(self):
        return _("Creating new tutorial")

    def form_valid(self, form):
        with revisions.create_revision(atomic=True):
            tutorial = form.save()
            tutorial.slug = self.request.user.username.lower()
            tutorial.publish_on = timezone.now()
            tutorial.authors.add(self.request.user.profile)
            tutorial.save()

            revisions.set_comment(_("Created on site"))
            revisions.set_user(self.request.user)

        return HttpResponseRedirect(tutorial.get_absolute_url())

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied()

        # Check minimum problem count requirement (same as blog posts)
        if (
            request.official_contest_mode
            or request.user.profile.problem_count < settings.VNOJ_BLOG_MIN_PROBLEM_COUNT
            and not request.user.is_superuser
            and not hasattr(self, "organization")
        ):
            return generic_message(
                request,
                _("Permission denied"),
                _(
                    "You cannot create tutorial.\n"
                    "Note: You need to solve at least %d problems to create new tutorial."
                )
                % settings.VNOJ_BLOG_MIN_PROBLEM_COUNT,
            )
        return super().dispatch(request, *args, **kwargs)


class TutorialEdit(TutorialMixin, TitleMixin, UpdateView):
    template_name = "tutorial/edit.html"
    model = Tutorial
    form_class = TutorialForm

    def get_title(self):
        return _("Updating tutorial")

    def get_content_title(self):
        return _("Updating tutorial")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["edit"] = True
        return context

    def form_valid(self, form):
        with revisions.create_revision(atomic=True):
            revisions.set_comment(_("Edited from site"))
            revisions.set_user(self.request.user)
            return super(TutorialEdit, self).form_valid(form)

    def get_success_url(self):
        return self.object.get_absolute_url()

    def dispatch(self, request, *args, **kwargs):
        if request.official_contest_mode and not request.user.is_superuser:
            return generic_message(
                request, _("Permission denied"), _("You cannot edit tutorial.")
            )
        return super().dispatch(request, *args, **kwargs)


class TutorialDelete(TutorialMixin, TitleMixin, DeleteView):
    model = Tutorial
    template_name = "tutorial/delete.html"

    def get_title(self):
        return _("Delete tutorial: %s") % self.object.title

    def get_content_title(self):
        return _("Delete tutorial: %s") % self.object.title

    def get_success_url(self):
        return reverse("tutorial_list")

    def dispatch(self, request, *args, **kwargs):
        if request.official_contest_mode and not request.user.is_superuser:
            return generic_message(
                request, _("Permission denied"), _("You cannot delete tutorial.")
            )
        return super().dispatch(request, *args, **kwargs)


# Legacy code execution classes removed - use tutorial_runner.py with Piston API instead
