import csv
import re
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.models import Group, User
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import transaction
from django.db.models import Count, FilteredRelation, Q, FloatField
from django.db.models.expressions import F, Value, RawSQL
from django.db.models.functions import Coalesce, Cast
from django.forms import Form, modelformset_factory
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext as _, gettext_lazy, ngettext
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView, View
from django.views.generic.detail import SingleObjectMixin, SingleObjectTemplateResponseMixin
from reversion import revisions

from judge.forms import OrganizationForm
from judge.models import BlogPost, Comment, Contest, Language, Organization, OrganizationRequest, \
    Problem, Profile
from judge.tasks import on_new_problem
from judge.utils.ranker import ranker
from judge.utils.views import DiggPaginatorMixin, QueryStringSortMixin, TitleMixin, generic_message
from judge.views.blog import BlogPostCreate, PostListBase
from judge.views.contests import ContestList, CreateContest
from judge.views.problem import ProblemCreate, ProblemList
from judge.views.submission import SubmissionsListBase

__all__ = ['OrganizationList', 'OrganizationHome', 'OrganizationUsers', 'OrganizationMembershipChange',
           'JoinOrganization', 'LeaveOrganization', 'EditOrganization', 'RequestJoinOrganization',
           'OrganizationRequestDetail', 'OrganizationRequestView', 'OrganizationRequestLog',
           'KickUserWidgetView']


class OrganizationMixin(object):
    context_object_name = 'organization'
    model = Organization

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['logo_override_image'] = self.object.logo_override_image
        context['meta_description'] = self.object.about[:settings.DESCRIPTION_MAX_LENGTH]
        return context

    def dispatch(self, request, *args, **kwargs):
        try:
            return super(OrganizationMixin, self).dispatch(request, *args, **kwargs)
        except Http404:
            key = kwargs.get(self.slug_url_kwarg, None)
            if key:
                return generic_message(request, _('No such organization'),
                                       _('Could not find an organization with the key "%s".') % key)
            else:
                return generic_message(request, _('No such organization'),
                                       _('Could not find such organization.'))

    def can_edit_organization(self, org=None):
        if org is None:
            org = self.object
        if not self.request.user.is_authenticated:
            return False
        return org.is_admin(self.request.profile)


class BaseOrganizationListView(OrganizationMixin, ListView):
    model = None
    context_object_name = None
    slug_url_kwarg = 'slug'

    def get_object(self):
        return get_object_or_404(Organization, id=self.kwargs.get('pk'))

    def get_context_data(self, **kwargs):
        return super().get_context_data(organization=self.object, **kwargs)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().get(request, *args, **kwargs)


class OrganizationDetailView(OrganizationMixin, DetailView):
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.slug != kwargs['slug']:
            return HttpResponsePermanentRedirect(reverse(
                request.resolver_match.url_name, args=(self.object.id, self.object.slug)))
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)


class OrganizationList(TitleMixin, ListView):
    model = Organization
    context_object_name = 'organizations'
    template_name = 'organization/list.html'
    title = gettext_lazy('Organizations')

    def get_queryset(self):
        return Organization.objects.filter(is_unlisted=False)


class OrganizationUsers(QueryStringSortMixin, DiggPaginatorMixin, BaseOrganizationListView):
    template_name = 'organization/users.html'
    all_sorts = frozenset(('points', 'problem_count', 'rating', 'performance_points'))
    default_desc = all_sorts
    default_sort = '-performance_points'
    paginate_by = 100
    context_object_name = 'users'

    def get_queryset(self):
        organization_id = self.object.id
        json_path_points = f'$.\"{organization_id}\"[0]'
        json_path_submissions = f'$.\"{organization_id}\"[1]'
        users = self.object.members.filter(is_unlisted=False).annotate(
                org_points=Cast(
                    RawSQL(
                        "JSON_EXTRACT(organization_points, %s)", (json_path_points,)
                        ),
                    FloatField()
                ),
                submission_count=RawSQL(
                    "IFNULL(CAST(JSON_UNQUOTE(JSON_EXTRACT(organization_points, %s)) AS UNSIGNED), 0)", (json_path_submissions,)
                )
            )
        # query_set = self.object.members.filter(is_unlisted=False).order_by(self.order) \
        #     .select_related('user', 'display_badge').defer('about', 'user_script', 'notes')
        # print(query_set.query)
        # return query_set

        if self.order == 'performance_points':
            query_order = 'org_points'
        elif self.order == '-performance_points':
            query_order = '-org_points'
        elif self.order == 'problem_count':
            query_order = 'submission_count'
        elif self.order == '-problem_count':
            query_order = '-submission_count'
        else:
            query_order = self.order

        query_set = users.order_by(query_order).select_related('user', 'display_badge').defer('about', 'user_script', 'notes')
        print(query_set.query)
        return query_set

    def get_context_data(self, **kwargs):
        context = super(OrganizationUsers, self).get_context_data(**kwargs)
        context['title'] = self.object.name
        context['users'] = ranker(context['users'])
        context['partial'] = True
        context['is_admin'] = self.can_edit_organization()
        context['kick_url'] = reverse('organization_user_kick', args=[self.object.id, self.object.slug])
        context['first_page_href'] = '.'
        context.update(self.get_sort_context())
        context.update(self.get_sort_paginate_context())
        return context


class OrganizationMembershipChange(LoginRequiredMixin, OrganizationMixin, SingleObjectMixin, View):
    def post(self, request, *args, **kwargs):
        org = self.get_object()
        response = self.handle(request, org, request.profile)
        if response is not None:
            return response
        return HttpResponseRedirect(org.get_absolute_url())

    def handle(self, request, org, profile):
        raise NotImplementedError()


class JoinOrganization(OrganizationMembershipChange):
    def handle(self, request, org, profile):
        if profile.organizations.filter(id=org.id).exists():
            return generic_message(request, _('Joining organization'), _('You are already in the organization.'))

        if not org.is_open:
            return generic_message(request, _('Joining organization'), _('This organization is not open.'))

        max_orgs = settings.DMOJ_USER_MAX_ORGANIZATION_COUNT
        if profile.organizations.filter(is_open=True).count() >= max_orgs:
            return generic_message(
                request, _('Joining organization'),
                ngettext('You may not be part of more than {count} public organization.',
                         'You may not be part of more than {count} public organizations.',
                         max_orgs).format(count=max_orgs),
            )

        profile.organizations.add(org)
        profile.save()


class LeaveOrganization(OrganizationMembershipChange):
    def handle(self, request, org, profile):
        if not profile.organizations.filter(id=org.id).exists():
            return generic_message(request, _('Leaving organization'), _('You are not in "%s".') % org.short_name)
        if org.is_admin(profile):
            return generic_message(request, _('Leaving organization'), _('You cannot leave an organization you own.'))
        profile.organizations.remove(org)


class OrganizationRequestForm(Form):
    reason = forms.CharField(widget=forms.Textarea)


class RequestJoinOrganization(LoginRequiredMixin, SingleObjectMixin, FormView):
    model = Organization
    slug_field = 'key'
    slug_url_kwarg = 'key'
    template_name = 'organization/requests/request.html'
    form_class = OrganizationRequestForm

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.requests.filter(user=self.request.profile, state='P').exists():
            return generic_message(self.request, _("Can't request to join %s") % self.object.name,
                                   _('You already have a pending request to join %s.') % self.object.name)
        return super(RequestJoinOrganization, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(RequestJoinOrganization, self).get_context_data(**kwargs)
        if self.object.is_open:
            raise Http404()
        context['title'] = _('Request to join %s') % self.object.name
        return context

    def form_valid(self, form):
        request = OrganizationRequest()
        request.organization = self.get_object()
        request.user = self.request.profile
        request.reason = form.cleaned_data['reason']
        request.state = 'P'
        request.save()
        return HttpResponseRedirect(reverse('request_organization_detail', args=(
            request.organization.id, request.organization.slug, request.id,
        )))


class OrganizationRequestDetail(LoginRequiredMixin, TitleMixin, DetailView):
    model = OrganizationRequest
    template_name = 'organization/requests/detail.html'
    title = gettext_lazy('Join request detail')
    pk_url_kwarg = 'rpk'

    def get_object(self, queryset=None):
        object = super(OrganizationRequestDetail, self).get_object(queryset)
        profile = self.request.profile
        if object.user_id != profile.id and not object.organization.is_admin(profile):
            raise PermissionDenied()
        return object


OrganizationRequestFormSet = modelformset_factory(OrganizationRequest, extra=0, fields=('state',), can_delete=True)


class OrganizationRequestBaseView(LoginRequiredMixin, SingleObjectTemplateResponseMixin, SingleObjectMixin, View):
    model = Organization
    slug_field = 'key'
    slug_url_kwarg = 'key'
    tab = None

    def get_object(self, queryset=None):
        organization = super(OrganizationRequestBaseView, self).get_object(queryset)
        if not organization.is_admin(self.request.profile):
            raise PermissionDenied()
        return organization

    def get_requests(self):
        queryset = self.object.requests.select_related('user__user').defer(
            'user__about', 'user__notes', 'user__user_script',
        )
        return queryset

    def get_context_data(self, **kwargs):
        context = super(OrganizationRequestBaseView, self).get_context_data(**kwargs)
        context['title'] = _('Managing join requests for %s') % self.object.name
        context['content_title'] = format_html(_('Managing join requests for %s') %
                                               ' <a href="{1}">{0}</a>', self.object.name,
                                               self.object.get_absolute_url())
        context['tab'] = self.tab
        return context


class OrganizationRequestView(OrganizationRequestBaseView):
    template_name = 'organization/requests/pending.html'
    tab = 'pending'

    def get_context_data(self, **kwargs):
        context = super(OrganizationRequestView, self).get_context_data(**kwargs)
        context['formset'] = self.formset
        return context

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.formset = OrganizationRequestFormSet(queryset=self.get_requests())
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_requests(self):
        return super().get_requests().filter(state='P')

    def post(self, request, *args, **kwargs):
        self.object = organization = self.get_object()
        self.formset = formset = OrganizationRequestFormSet(request.POST, request.FILES, queryset=self.get_requests())
        if formset.is_valid():
            if organization.slots is not None:
                deleted_set = set(formset.deleted_forms)
                to_approve = sum(form.cleaned_data['state'] == 'A' for form in formset.forms if form not in deleted_set)
                can_add = organization.slots - organization.members.count()
                if to_approve > can_add:
                    msg1 = ngettext('Your organization can only receive %d more member.',
                                    'Your organization can only receive %d more members.', can_add) % can_add
                    msg2 = ngettext('You cannot approve %d user.',
                                    'You cannot approve %d users.', to_approve) % to_approve
                    messages.error(request, msg1 + '\n' + msg2)
                    return self.render_to_response(self.get_context_data(object=organization))

            approved, rejected = 0, 0
            for obj in formset.save():
                if obj.state == 'A':
                    obj.user.organizations.add(obj.organization)
                    approved += 1
                elif obj.state == 'R':
                    rejected += 1
            messages.success(request,
                             ngettext('Approved %d user.', 'Approved %d users.', approved) % approved + '\n' +
                             ngettext('Rejected %d user.', 'Rejected %d users.', rejected) % rejected)
            return HttpResponseRedirect(request.get_full_path())
        return self.render_to_response(self.get_context_data(object=organization))

    put = post


class OrganizationRequestLog(OrganizationRequestBaseView):
    states = ('A', 'R')
    tab = 'log'
    template_name = 'organization/requests/log.html'

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super(OrganizationRequestLog, self).get_context_data(**kwargs)
        context['requests'] = self.get_requests().filter(state__in=self.states)
        return context


class CreateOrganization(PermissionRequiredMixin, TitleMixin, CreateView):
    template_name = 'organization/edit.html'
    model = Organization
    form_class = OrganizationForm
    permission_required = 'judge.add_organization'

    def get_title(self):
        return _('Create new organization')

    def form_valid(self, form):
        with revisions.create_revision(atomic=True):
            revisions.set_comment(_('Created on site'))
            revisions.set_user(self.request.user)

            self.object = org = form.save()
            # slug is show in url
            # short_name is show in ranking
            org.short_name = org.slug[:20]
            org.save()
            all_admins = org.admins.all()
            g = Group.objects.get(name=settings.GROUP_PERMISSION_FOR_ORG_ADMIN)
            for admin in all_admins:
                admin.user.groups.add(g)

            return HttpResponseRedirect(self.get_success_url())

    def dispatch(self, request, *args, **kwargs):
        if self.has_permission():
            if self.request.user.profile.admin_of.count() >= settings.VNOJ_ORGANIZATION_ADMIN_LIMIT and \
               not self.request.user.has_perm('spam_organization'):
                return render(request, 'organization/create-limit-error.html', {
                    'admin_of': self.request.user.profile.admin_of.all(),
                    'admin_limit': settings.VNOJ_ORGANIZATION_ADMIN_LIMIT,
                    'title': _("Can't create organization"),
                }, status=403)
            return super(CreateOrganization, self).dispatch(request, *args, **kwargs)
        else:
            return generic_message(request, _("Can't create organization"),
                                   _('You are not allowed to create new organizations.'), status=403)


class EditOrganization(LoginRequiredMixin, TitleMixin, OrganizationMixin, UpdateView):
    template_name = 'organization/edit.html'
    model = Organization
    form_class = OrganizationForm

    def get_title(self):
        return _('Editing %s') % self.object.name

    def get_object(self, queryset=None):
        object = super(EditOrganization, self).get_object()
        if not self.can_edit_organization(object):
            raise PermissionDenied()
        return object

    def form_valid(self, form):
        with revisions.create_revision(atomic=True):
            revisions.set_comment(_('Edited from site'))
            revisions.set_user(self.request.user)
            return super(EditOrganization, self).form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        try:
            return super(EditOrganization, self).dispatch(request, *args, **kwargs)
        except PermissionDenied:
            return generic_message(request, _("Can't edit organization"),
                                   _('You are not allowed to edit this organization.'), status=403)


class KickUserWidgetView(LoginRequiredMixin, OrganizationMixin, SingleObjectMixin, View):
    def post(self, request, *args, **kwargs):
        organization = self.get_object()
        if not self.can_edit_organization(organization):
            return generic_message(request, _("Can't edit organization"),
                                   _('You are not allowed to kick people from this organization.'), status=403)

        try:
            user = Profile.objects.get(id=request.POST.get('user', None))
        except Profile.DoesNotExist:
            return generic_message(request, _("Can't kick user"),
                                   _('The user you are trying to kick does not exist!'), status=400)

        if not organization.members.filter(id=user.id).exists():
            return generic_message(request, _("Can't kick user"),
                                   _('The user you are trying to kick is not in organization: %s') %
                                   organization.name, status=400)

        if organization.admins.filter(id=user.id).exists():
            return generic_message(request, _("Can't kick user"),
                                   _('The user you are trying to kick is an admin of organization: %s.') %
                                   organization.name, status=400)

        organization.members.remove(user)
        return HttpResponseRedirect(organization.get_users_url())


# This is almost the same as the OrganizationMixin
# However, I need to write a new class because the
# current mixin is for the DetailView.
class CustomOrganizationMixin(object):
    # If true, all user can view the current page
    # even if they are not in the org
    allow_all_users = False

    # If the user has at least one of the following permissions,
    # they can access the private data even if they are not in the org
    permission_bypass = []

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organization'] = self.organization
        context['logo_override_image'] = self.organization.logo_override_image
        context['meta_description'] = self.organization.about[:settings.DESCRIPTION_MAX_LENGTH]
        return context

    def dispatch(self, request, *args, **kwargs):
        if 'pk' not in kwargs:
            raise ImproperlyConfigured('Must pass a pk')
        self.organization = get_object_or_404(Organization, pk=kwargs['pk'])
        self.object = self.organization

        if not self.allow_all_users and \
           self.request.profile not in self.organization and \
           not any(self.request.user.has_perm(perm) for perm in self.permission_bypass):
            return generic_message(request,
                                   _("Cannot view organization's private data"),
                                   _('You must join the organization to view its private data.'))

        return super(CustomOrganizationMixin, self).dispatch(request, *args, **kwargs)

    def can_edit_organization(self, org=None):
        if org is None:
            org = self.organization
        if not self.request.user.is_authenticated:
            return False
        return org.is_admin(self.request.profile)


class CustomAdminOrganizationMixin(CustomOrganizationMixin):
    def dispatch(self, request, *args, **kwargs):
        if 'pk' not in kwargs:
            raise ImproperlyConfigured('Must pass a pk')
        self.organization = get_object_or_404(Organization, pk=kwargs['pk'])
        if self.can_edit_organization():
            return super(CustomAdminOrganizationMixin, self).dispatch(request, *args, **kwargs)
        raise PermissionDenied

    def get_form_kwargs(self):
        kwargs = super(CustomAdminOrganizationMixin, self).get_form_kwargs()
        kwargs['org_pk'] = self.organization.pk
        return kwargs


class OrganizationHome(TitleMixin, CustomOrganizationMixin, PostListBase):
    template_name = 'organization/home.html'
    # Need to set this to true so user can view the org's public
    # information like name, request join org, ...
    # However, they cannot see the org blog
    allow_all_users = True

    def get_queryset(self):
        queryset = BlogPost.objects.filter(organization=self.organization)

        if not self.request.user.has_perm('judge.edit_all_post'):
            if not self.can_edit_organization():
                if self.request.profile in self.object:
                    # Normal user can only view public posts
                    queryset = queryset.filter(publish_on__lte=timezone.now(), visible=True)
                else:
                    # User cannot view organization blog
                    # if they are not in the org
                    # even if the org is public
                    return BlogPost.objects.none()
            else:
                # Org admin can view public posts & their own posts
                queryset = queryset.filter(Q(visible=True) | Q(authors=self.request.profile))

        if self.request.user.is_authenticated:
            profile = self.request.profile
            queryset = queryset.annotate(
                my_vote=FilteredRelation('votes', condition=Q(votes__voter_id=profile.id)),
            ).annotate(vote_score=Coalesce(F('my_vote__score'), Value(0)))

        return queryset.order_by('-sticky', '-publish_on').prefetch_related('authors__user')

    def get_context_data(self, **kwargs):
        context = super(OrganizationHome, self).get_context_data(**kwargs)
        context['first_page_href'] = reverse('organization_home', args=[self.object.pk, self.object.slug])
        context['title'] = self.object.name
        context['can_edit'] = self.can_edit_organization()
        context['is_member'] = self.request.profile in self.object

        context['post_comment_counts'] = {
            int(page[2:]): count for page, count in
            Comment.objects
                   .filter(page__in=['b:%d' % post.id for post in context['posts']], hidden=False)
                   .values_list('page').annotate(count=Count('page')).order_by()
        }

        if not self.object.is_open:
            context['num_requests'] = OrganizationRequest.objects.filter(
                state='P',
                organization=self.object).count()

        user = self.request.user
        if context['is_member'] or \
           user.has_perm('judge.see_organization_problem') or \
           user.has_perm('judge.edit_all_problem'):
            context['new_problems'] = Problem.objects.filter(
                is_public=True, is_organization_private=True,
                organizations=self.object) \
                .order_by('-date', '-id')[:settings.DMOJ_BLOG_NEW_PROBLEM_COUNT]

        see_private_contest = user.has_perm('judge.see_private_contest') or user.has_perm('judge.edit_all_contest')
        if context['is_member'] or see_private_contest:
            new_contests = Contest.objects.filter(
                is_visible=True, is_organization_private=True,
                organizations=self.object) \
                .order_by('-end_time', '-id')

            if not see_private_contest:
                _filter = Q(is_private=False)
                if user.is_authenticated:
                    _filter |= Q(private_contestants=user.profile)
                new_contests = new_contests.filter(_filter)

            context['new_contests'] = new_contests[:settings.DMOJ_BLOG_NEW_PROBLEM_COUNT]

        return context


class ProblemListOrganization(CustomOrganizationMixin, ProblemList):
    context_object_name = 'problems'
    template_name = 'organization/problem-list.html'
    permission_bypass = ['judge.see_organization_problem', 'judge.edit_all_problem']

    def get_hot_problems(self):
        return None

    def get_context_data(self, **kwargs):
        context = super(ProblemListOrganization, self).get_context_data(**kwargs)
        context['title'] = self.organization.name
        return context

    def get_filter(self):
        """Get filter for visible problems in an organization

        The logic of this is:
            - If user has perm `see_private_problem`, they
            can view all org's problem (including private problems)
            - Otherwise, they can view all public problems and
            problems that they are authors/curators/testers

        With that logic, Organization admins cannot view private
        problems of other admins unless they are authors/curators/testers
        """
        if self.request.user.has_perm('judge.see_private_problem'):
            return Q(organizations=self.organization)

        _filter = Q(is_public=True)

        # Authors, curators, and testers should always have access, so OR at the very end.
        if self.profile is not None:
            _filter |= Q(authors=self.profile)
            _filter |= Q(curators=self.profile)
            _filter |= Q(testers=self.profile)

        return _filter & Q(organizations=self.organization)


class ContestListOrganization(CustomOrganizationMixin, ContestList):
    template_name = 'organization/contest-list.html'
    permission_bypass = ['judge.see_private_contest', 'judge.edit_all_contest']
    hide_private_contests = None

    def _get_queryset(self):
        query_set = super(ContestListOrganization, self)._get_queryset()
        query_set = query_set.filter(is_organization_private=True, organizations=self.organization)
        return query_set

    def get_context_data(self, **kwargs):
        context = super(ContestListOrganization, self).get_context_data(**kwargs)
        context['title'] = self.organization.name
        return context


class SubmissionListOrganization(CustomOrganizationMixin, SubmissionsListBase):
    template_name = 'organization/submission-list.html'
    permission_bypass = ['judge.view_all_submission']

    def _get_queryset(self):
        query_set = super(SubmissionListOrganization, self)._get_queryset()
        query_set = query_set.filter(problem__organizations=self.organization)
        return query_set

    def get_context_data(self, **kwargs):
        context = super(SubmissionListOrganization, self).get_context_data(**kwargs)
        context['title'] = self.organization.name
        context['content_title'] = self.organization.name
        return context


class ProblemCreateOrganization(CustomAdminOrganizationMixin, ProblemCreate):
    permission_required = 'judge.create_organization_problem'

    def get_initial(self):
        initial = super(ProblemCreateOrganization, self).get_initial()
        initial = initial.copy()
        initial['code'] = ''.join(x for x in self.organization.slug.lower() if x.isalpha()) + '_'
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({
            'user': self.request.user,
        })
        return kwargs

    def form_valid(self, form):
        with revisions.create_revision(atomic=True):
            self.object = problem = form.save()
            problem.authors.add(self.request.user.profile)
            problem.allowed_languages.set(Language.objects.filter(include_in_problem=True))

            problem.is_organization_private = True
            problem.organizations.add(self.organization)
            problem.date = timezone.now()
            self.save_statement(form, problem)
            problem.save()

            revisions.set_comment(_('Created on site'))
            revisions.set_user(self.request.user)

        on_new_problem.delay(problem.code)
        return HttpResponseRedirect(self.get_success_url())


class BlogPostCreateOrganization(CustomAdminOrganizationMixin, PermissionRequiredMixin, BlogPostCreate):
    permission_required = 'judge.edit_organization_post'

    def get_initial(self):
        initial = super(BlogPostCreateOrganization, self).get_initial()
        initial = initial.copy()
        initial['publish_on'] = timezone.now()
        return initial

    def form_valid(self, form):
        with revisions.create_revision(atomic=True):
            post = form.save()
            post.authors.add(self.request.user.profile)
            post.slug = ''.join(x for x in self.organization.slug.lower() if x.isalpha())  # Initial post slug
            post.organization = self.organization
            post.save()

            revisions.set_comment(_('Created on site'))
            revisions.set_user(self.request.user)

        return HttpResponseRedirect(post.get_absolute_url())


class ContestCreateOrganization(CustomAdminOrganizationMixin, CreateContest):
    permission_required = 'judge.create_private_contest'

    def get_initial(self):
        initial = super(ContestCreateOrganization, self).get_initial()
        initial = initial.copy()
        initial['key'] = ''.join(x for x in self.organization.slug.lower() if x.isalpha()) + '_'
        return initial

    def save_contest_form(self, form):
        self.object = form.save()
        self.object.authors.add(self.request.profile)
        self.object.is_organization_private = True
        self.object.organizations.add(self.organization)
        self.object.save()


# CSV User Import Functionality

def generate_username_from_email(email):
    """Generate unique username from email address"""
    # Take part before @ and clean it
    base = email.split('@')[0]
    
    # Remove special characters, keep only alphanumeric and underscore
    username = re.sub(r'[^\w]', '_', base.lower())
    
    # Ensure it doesn't start with a number (Django requirement)
    if username and username[0].isdigit():
        username = 'user_' + username
    
    # Handle duplicates by adding numbers
    original = username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{original}_{counter}"
        counter += 1
    
    return username


class CSVImportForm(forms.Form):
    csv_file = forms.FileField(
        label=_('CSV File'),
        help_text=_('Upload CSV file with columns: email, full_name, password (max 2MB, 100 users)'),
        widget=forms.FileInput(attrs={'accept': '.csv'})
    )
    
    def clean_csv_file(self):
        csv_file = self.cleaned_data.get('csv_file')
        
        if not csv_file:
            raise forms.ValidationError(_('Please select a CSV file.'))
        
        # Check file size (2MB limit)
        if csv_file.size > 2 * 1024 * 1024:
            raise forms.ValidationError(_('File too large. Maximum size is 2MB.'))
        
        # Check file extension
        if not csv_file.name.lower().endswith('.csv'):
            raise forms.ValidationError(_('Please upload a CSV file.'))
        
        return csv_file


class OrganizationUserImportView(CustomAdminOrganizationMixin, FormView):
    form_class = CSVImportForm
    template_name = 'organization/import_users.html'
    
    def get_form_kwargs(self):
        kwargs = super(FormView, self).get_form_kwargs()  # Skip CustomAdminOrganizationMixin's get_form_kwargs
        return kwargs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organization'] = self.organization
        context['title'] = _('Import Users - %s') % self.organization.name
        context['is_admin'] = self.can_edit_organization()
        return context
    
    def form_valid(self, form):
        csv_file = form.cleaned_data['csv_file']
        
        try:
            # Process CSV file
            result = self.process_csv_file(csv_file)
            
            # Add results to context for display
            context = self.get_context_data(form=form)
            context.update(result)
            context['import_completed'] = True
            
            return self.render_to_response(context)
            
        except Exception as e:
            form.add_error('csv_file', str(e))
            return super().form_invalid(form)
    
    def process_csv_file(self, csv_file):
        """Process uploaded CSV file and create users"""
        created_users = []
        skipped_users = []
        errors = []
        
        # Read and decode CSV file
        csv_content = csv_file.read().decode('utf-8-sig')  # utf-8-sig handles BOM
        csv_reader = csv.DictReader(csv_content.splitlines())
        
        # Validate CSV headers
        required_headers = ['email', 'full_name', 'password']
        optional_headers = ['username']
        if not all(header in csv_reader.fieldnames for header in required_headers):
            raise ValueError(_('CSV must contain columns: email, full_name, password. Username column is optional.'))
        
        rows = list(csv_reader)
        
        # Check row limit
        if len(rows) > 100:
            raise ValueError(_('Maximum 100 users allowed per import. Your file has %d rows.') % len(rows))
        
        with transaction.atomic():
            for row_num, row in enumerate(rows, start=2):  # Start at 2 because row 1 is headers
                try:
                    email = row.get('email', '').strip()
                    full_name = row.get('full_name', '').strip()
                    password = row.get('password', '').strip()
                    username = row.get('username', '').strip()
                    
                    # Validate required fields
                    if not email or not full_name or not password:
                        errors.append({
                            'row': row_num,
                            'error': _('Missing required fields (email, full_name, password)')
                        })
                        continue
                    
                    # Check if email already exists
                    if User.objects.filter(email=email).exists():
                        skipped_users.append({
                            'row': row_num,
                            'email': email,
                            'reason': _('Email already exists')
                        })
                        continue
                    
                    # Generate username if not provided
                    if not username:
                        username = generate_username_from_email(email)
                    else:
                        # Check if provided username already exists
                        if User.objects.filter(username=username).exists():
                            skipped_users.append({
                                'row': row_num,
                                'email': email,
                                'reason': _('Username already exists: %s') % username
                            })
                            continue
                    
                    # Create user
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        password=password,
                        first_name=full_name,
                        is_active=True  # Auto-activate
                    )
                    
                    # Create profile
                    profile, created = Profile.objects.get_or_create(
                        user=user,
                        defaults={
                            'language': Language.get_default_language(),
                            'timezone': settings.DEFAULT_USER_TIME_ZONE,
                        }
                    )
                    
                    # Add to organization
                    profile.organizations.add(self.organization)
                    
                    created_users.append({
                        'row': row_num,
                        'email': email,
                        'full_name': full_name,
                        'username': username
                    })
                    
                except Exception as e:
                    errors.append({
                        'row': row_num,
                        'error': str(e)
                    })
        
        return {
            'created_users': created_users,
            'skipped_users': skipped_users,
            'errors': errors,
            'total_created': len(created_users),
            'total_skipped': len(skipped_users),
            'total_errors': len(errors),
        }


class OrganizationUserImportTemplateView(CustomAdminOrganizationMixin, View):
    """Download CSV template for user import"""
    
    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="users_template.csv"'
        
        # Add UTF-8 BOM for Excel compatibility
        response.write('\ufeff')
        
        writer = csv.writer(response)
        
        # Write headers
        writer.writerow(['email', 'full_name', 'password', 'username'])
        
        # Write sample data with Vietnamese names
        writer.writerow(['admin@school.edu.vn', 'Quản Trị Viên', 'password123', 'admin'])
        writer.writerow(['teacher1@school.edu.vn', 'Nguyễn Văn Giáo', 'giaovien2024', ''])  # Empty username - will be generated
        writer.writerow(['student.2024@school.edu.vn', 'Trần Thị Học Sinh', 'hocsinh123', 'student2024'])
        writer.writerow(['pham.van.nam@school.edu.vn', 'Phạm Văn Nam', 'matkhau456', ''])  # Empty username - will be generated
        
        return response
