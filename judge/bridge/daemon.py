import logging
import signal
import threading
from functools import partial

from django.conf import settings

from judge.bridge.django_handler import DjangoHandler
from judge.bridge.judge_handler import JudgeHandler
from judge.bridge.judge_list import JudgeList
from judge.bridge.server import Server
from judge.models import Judge, Submission

logger = logging.getLogger('judge.bridge')


def reset_judges():
    logger.info('iniadasdasdas')
    print('starttttt')
    Judge.objects.update(online=False, ping=None, load=None)
    logger.info('adasdasdasdasd')

def judge_daemon():
    print('starttttt1')
    logger.info('start')
    reset_judges()
    logger.info('start ----------------------111111111111111111')
    Submission.objects.filter(status__in=Submission.IN_PROGRESS_GRADING_STATUS) \
        .update(status='IE', result='IE', error=None)
    judges = JudgeList()
    logger.info('start -------------------------222222222222222222')

    judge_server = Server(settings.BRIDGED_JUDGE_ADDRESS, partial(JudgeHandler, judges=judges))
    django_server = Server(settings.BRIDGED_DJANGO_ADDRESS, partial(DjangoHandler, judges=judges))
    logger.info('start ===================================')
    threading.Thread(target=django_server.serve_forever).start()
    threading.Thread(target=judge_server.serve_forever).start()

    stop = threading.Event()

    logger.info('start =================================== 3333333333333333333333333')
    def signal_handler(signum, _):
        logger.info('Exiting due to %s', signal.Signals(signum).name)
        stop.set()

    logger.info('start ==============444444444444444444')
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGQUIT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        stop.wait()
    finally:
        django_server.shutdown()
        judge_server.shutdown()
