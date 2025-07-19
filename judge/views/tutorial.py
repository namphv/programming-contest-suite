import uuid
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import FilteredRelation, Max, Q
from django.db.models.expressions import F, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, ListView, UpdateView, View
from reversion import revisions

from django.views.generic import DetailView
from judge.forms import TutorialForm
from judge.judgeapi import abort_submission, judge_submission
from judge.models import Contest, Language, Problem, Profile, Submission, SubmissionSource, Ticket, Tutorial
from judge.utils.diggpaginator import DiggPaginator
from judge.utils.opengraph import generate_opengraph
from judge.utils.tickets import filter_visible_tickets
from judge.utils.views import TitleMixin, generic_message


class TutorialMixin(object):
    model = Tutorial
    pk_url_kwarg = 'id'
    slug_url_kwarg = 'slug'

    def get_object(self, queryset=None):
        tutorial = super(TutorialMixin, self).get_object(queryset)
        if not tutorial.is_editable_by(self.request.user):
            raise PermissionDenied()
        return tutorial


class TutorialListBase(ListView):
    model = Tutorial
    paginate_by = 10
    context_object_name = 'tutorials'
    title = None

    def get_paginator(self, queryset, per_page, orphans=0,
                      allow_empty_first_page=True, **kwargs):
        return DiggPaginator(queryset, per_page, body=6, padding=2,
                             orphans=orphans, allow_empty_first_page=allow_empty_first_page, **kwargs)

    def get_queryset(self):
        queryset = (Tutorial.objects.filter(visible=True, publish_on__lte=timezone.now())
                    .prefetch_related('authors__user', 'authors__display_badge'))
        return queryset

    def get_context_data(self, **kwargs):
        context = super(TutorialListBase, self).get_context_data(**kwargs)
        context['first_page_href'] = None
        context['title'] = self.title or _('Page %d of Tutorials') % context['page_obj'].number
        return context


class TutorialList(TutorialListBase):
    template_name = 'tutorial/list.html'

    def get_queryset(self):
        queryset = super(TutorialList, self).get_queryset()
        
        # Filter by organization if specified
        if self.request.user.is_authenticated and hasattr(self.request, 'organization'):
            queryset = queryset.filter(organization=self.request.organization)
        else:
            queryset = queryset.filter(organization=None)

        queryset = queryset.order_by('-publish_on')
        return queryset

    def get_context_data(self, **kwargs):
        context = super(TutorialList, self).get_context_data(**kwargs)
        context['first_page_href'] = reverse('tutorial_list')
        context['page_prefix'] = reverse('tutorial_list')
        
        # Add navigation links
        context['tutorial_list_link'] = reverse('tutorial_list')
        context['newsfeed_link'] = reverse('home')
        context['all_blogs_link'] = reverse('blog_post_list')
        
        return context


class TutorialView(TitleMixin, DetailView):
    model = Tutorial
    pk_url_kwarg = 'id'
    context_object_name = 'tutorial'
    template_name = 'tutorial/content.html'

    def get_title(self):
        return self.object.title

    def get_context_data(self, **kwargs):
        context = super(TutorialView, self).get_context_data(**kwargs)

        metadata = generate_opengraph('generated-meta-tutorial:%d' % self.object.id,
                                      self.object.summary or self.object.content, 'tutorial')
        context['meta_description'] = metadata[0]
        context['og_image'] = metadata[1]

        return context

    def get_object(self, queryset=None):
        tutorial = super(TutorialView, self).get_object(queryset)
        if not tutorial.can_see(self.request.user):
            raise Http404()
        return tutorial


class TutorialCreate(TitleMixin, CreateView):
    template_name = 'tutorial/edit.html'
    model = Tutorial
    form_class = TutorialForm

    def get_title(self):
        return _('Creating new tutorial')

    def get_content_title(self):
        return _('Creating new tutorial')

    def form_valid(self, form):
        with revisions.create_revision(atomic=True):
            tutorial = form.save()
            tutorial.slug = self.request.user.username.lower()
            tutorial.publish_on = timezone.now()
            tutorial.authors.add(self.request.user.profile)
            tutorial.save()

            revisions.set_comment(_('Created on site'))
            revisions.set_user(self.request.user)

        return HttpResponseRedirect(tutorial.get_absolute_url())

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied()
        
        # Check minimum problem count requirement (same as blog posts)
        if request.official_contest_mode or request.user.profile.problem_count < settings.VNOJ_BLOG_MIN_PROBLEM_COUNT \
                and not request.user.is_superuser and not hasattr(self, 'organization'):
            return generic_message(request, _('Permission denied'),
                                   _('You cannot create tutorial.\n'
                                     'Note: You need to solve at least %d problems to create new tutorial.')
                                   % settings.VNOJ_BLOG_MIN_PROBLEM_COUNT)
        return super().dispatch(request, *args, **kwargs)


class TutorialEdit(TutorialMixin, TitleMixin, UpdateView):
    template_name = 'tutorial/edit.html'
    model = Tutorial
    form_class = TutorialForm

    def get_title(self):
        return _('Updating tutorial')

    def get_content_title(self):
        return _('Updating tutorial')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['edit'] = True
        return context

    def form_valid(self, form):
        with revisions.create_revision(atomic=True):
            revisions.set_comment(_('Edited from site'))
            revisions.set_user(self.request.user)
            return super(TutorialEdit, self).form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if request.official_contest_mode and not request.user.is_superuser:
            return generic_message(request, _('Permission denied'),
                                   _('You cannot edit tutorial.'))
        return super().dispatch(request, *args, **kwargs)


@method_decorator(csrf_exempt, name='dispatch')
class TutorialRunCode(View):
    """
    API endpoint for executing Python code in tutorials.
    Leverages the existing judge system for secure execution.
    """
    
    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
            
        try:
            import json
            data = json.loads(request.body)
            code = data.get('code', '').strip()
            
            if not code:
                return JsonResponse({'error': 'No code provided'}, status=400)
            
            if len(code) > 65536:  # Same limit as regular submissions
                return JsonResponse({'error': 'Code too long'}, status=400)
                
            # Get Python language
            try:
                python_lang = Language.objects.get(key='PY3')
            except Language.DoesNotExist:
                return JsonResponse({'error': 'Python language not available'}, status=500)
            
            # Get or create default problem group
            from judge.models import ProblemGroup
            default_group, created = ProblemGroup.objects.get_or_create(
                name='System Problems',
                defaults={'full_name': 'System Problems'}
            )
            
            # Create a virtual "tutorial runner" problem if it doesn't exist
            tutorial_problem, created = Problem.objects.get_or_create(
                code='__TUTORIAL_PYTHON_RUNNER__',
                defaults={
                    'name': 'Tutorial Python Runner (Internal)',
                    'description': 'Virtual problem for tutorial code execution',
                    'points': 0,
                    'time_limit': 2.0,  # 2 second limit for tutorials
                    'memory_limit': 64000,  # 64MB limit
                    'is_public': False,
                    'is_manually_managed': True,
                    'group': default_group,
                }
            )
            
            # Create submission for tutorial execution
            submission = Submission(
                user=request.user.profile,  # Use Profile, not User
                problem=tutorial_problem,
                language=python_lang,
                # Don't set time here, let auto_now_add handle it
                case_points=0,
                case_total=0,
                points=0,
                result='QU',  # Queued
                status='QU',
                current_testcase=0
            )
            submission.save()
            
            # Create submission source
            source = SubmissionSource(
                submission=submission,
                source=code
            )
            source.save()
            
            # Generate execution ID for real-time tracking
            execution_id = str(uuid.uuid4())
            
            # Check if there are any online judges available
            from judge.models import Judge
            online_judges = Judge.objects.filter(online=True).count()
            
            if online_judges == 0:
                # No judges available, use local execution
                print("No judges available, using local execution")
                
                # Execute Python code locally and capture output
                try:
                    import subprocess
                    import tempfile
                    import os
                    import time
                    
                    # Create a temporary file for the code
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                        f.write(code)
                        temp_file = f.name
                    
                    try:
                        # Execute the code with a timeout
                        start_time = time.time()
                        result = subprocess.run(
                            ['python3', temp_file],
                            capture_output=True,
                            text=True,
                            timeout=5,  # 5 second timeout for safety
                            cwd='/tmp'  # Safe working directory
                        )
                        execution_time = time.time() - start_time
                        
                        # Clean up the temporary file
                        os.unlink(temp_file)
                        
                        # Determine the result
                        if result.returncode == 0:
                            # Successful execution
                            output = result.stdout
                            if result.stderr:
                                output += "\n--- Warnings ---\n" + result.stderr
                            
                            submission.status = 'D'  # Done
                            submission.result = 'AC'  # Accepted
                            submission.time = execution_time
                            submission.memory = 1024  # Mock memory usage
                            submission.points = 0
                            submission.error = output  # Store output in error field for now
                            submission.save()
                            
                            return JsonResponse({
                                'success': True,
                                'submission_id': submission.id,
                                'execution_id': execution_id,
                                'message': 'Code executed successfully'
                            })
                        else:
                            # Runtime error
                            error_output = result.stderr or result.stdout or 'Unknown error'
                            
                            submission.status = 'D'  # Done
                            submission.result = 'RTE'  # Runtime Error
                            submission.time = execution_time
                            submission.memory = 1024
                            submission.points = 0
                            submission.error = error_output
                            submission.save()
                            
                            return JsonResponse({
                                'success': True,
                                'submission_id': submission.id,
                                'execution_id': execution_id,
                                'message': 'Code executed with errors'
                            })
                            
                    except subprocess.TimeoutExpired:
                        # Clean up the temporary file
                        os.unlink(temp_file)
                        
                        # Time limit exceeded
                        submission.status = 'D'  # Done
                        submission.result = 'TLE'  # Time Limit Exceeded
                        submission.time = 5.0
                        submission.memory = 1024
                        submission.points = 0
                        submission.error = 'Time limit exceeded (5 seconds)'
                        submission.save()
                        
                        return JsonResponse({
                            'success': True,
                            'submission_id': submission.id,
                            'execution_id': execution_id,
                            'message': 'Code execution timed out'
                        })
                        
                    except Exception as exec_error:
                        # Clean up the temporary file if it exists
                        if os.path.exists(temp_file):
                            os.unlink(temp_file)
                        raise exec_error
                        
                except SyntaxError as syntax_error:
                    # Handle syntax errors
                    submission.status = 'D'  # Done
                    submission.result = 'CE'  # Compile Error
                    submission.time = 0
                    submission.memory = 0
                    submission.points = 0
                    submission.error = str(syntax_error)
                    submission.save()
                    
                    return JsonResponse({
                        'success': True,
                        'submission_id': submission.id,
                        'execution_id': execution_id,
                        'message': 'Code has syntax errors'
                    })
                
                except Exception as exec_error:
                    # Clean up on failure
                    submission.delete()
                    return JsonResponse({'error': f'Execution failed: {str(exec_error)}'}, status=500)
            
            else:
                # Judges available, use real execution
                try:
                    judge_submission(submission, rejudge=False, batch_rejudge=False, judge_id=None)
                    
                    return JsonResponse({
                        'success': True,
                        'submission_id': submission.id,
                        'execution_id': execution_id,
                        'message': 'Code submitted for execution'
                    })
                    
                except Exception as e:
                    # Clean up on failure
                    submission.delete()
                    return JsonResponse({'error': f'Execution failed: {str(e)}'}, status=500)
                
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            return JsonResponse({'error': f'Internal error: {str(e)}'}, status=500)


class TutorialExecutionStatus(View):
    """
    API endpoint to check the status of tutorial code execution.
    """
    
    def get(self, request, submission_id):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
            
        try:
            submission = Submission.objects.get(
                id=submission_id,
                user=request.user.profile,
                problem__code='__TUTORIAL_PYTHON_RUNNER__'
            )
            
            response_data = {
                'status': submission.status,
                'result': submission.result,
                'time': submission.time_memory.time if hasattr(submission, 'time_memory') and submission.time_memory else None,
                'memory': submission.time_memory.memory if hasattr(submission, 'time_memory') and submission.time_memory else None,
            }
            
            # Add output if execution is complete
            if submission.status in ['D', 'IE', 'CE', 'AB']:  # Done, Internal Error, Compile Error, Aborted
                try:
                    # Get the submission source and any error messages
                    if hasattr(submission, 'submission_source'):
                        source = submission.submission_source.source
                        response_data['source'] = source
                        
                    # For tutorial execution, provide the actual output
                    if submission.result == 'AC':
                        # For successful execution, show the actual output
                        output = submission.error if submission.error else 'Code executed successfully with no output'
                        response_data['output'] = output
                        response_data['success'] = True
                    elif submission.result == 'CE':
                        error_msg = submission.error if submission.error else 'Syntax error in code'
                        response_data['output'] = f'Syntax Error:\n{error_msg}'
                        response_data['success'] = False
                    elif submission.result == 'TLE':
                        error_msg = submission.error if submission.error else 'Time Limit Exceeded (5 seconds)'
                        response_data['output'] = error_msg
                        response_data['success'] = False
                    elif submission.result == 'MLE':
                        error_msg = submission.error if submission.error else 'Memory Limit Exceeded'
                        response_data['output'] = error_msg
                        response_data['success'] = False
                    elif submission.result == 'RTE':
                        error_msg = submission.error if submission.error else 'Runtime Error'
                        response_data['output'] = f'Runtime Error:\n{error_msg}'
                        response_data['success'] = False
                    else:
                        response_data['output'] = f'Execution result: {submission.result}'
                        response_data['success'] = submission.result == 'AC'
                        
                except Exception:
                    response_data['output'] = 'Error retrieving execution output'
                    response_data['success'] = False
            else:
                response_data['output'] = 'Execution in progress...'
                response_data['success'] = None
            
            return JsonResponse(response_data)
            
        except Submission.DoesNotExist:
            return JsonResponse({'error': 'Submission not found'}, status=404)
        except Exception as e:
            return JsonResponse({'error': f'Error checking status: {str(e)}'}, status=500)