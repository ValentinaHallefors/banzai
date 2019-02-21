import os

## Broker settings.
BROKER_URL = '{broker_url}'.format(broker_url=os.getenv('RABBITMQ_BROKER', default='amqp://guest@rabbitmq'))

# List of modules to import when celery starts.
CELERY_IMPORTS     = ('banzai')
#CELERY_ACCEPT_CONTENT = ['pickle']
CELERY_ANNOTATIONS = {'banzai.main.reduce_single_frame': {'ignore_result': True}}

CELERYD_CONCURRENCY         = int(os.getenv('CELERY_CONCURRENCY', default='2'))
CELERYD_LOG_FILE            = '{log_directory}/%h-worker-%i.log'.format(log_directory=os.getenv('LOG_DIRECTORY',
                                                                                                default='/home/mturner/scratch/logs/'))
CELERYD_LOG_LEVEL           = os.getenv('CELERY_LOG_LEVEL', default='info')
CELERY_IGNORE_RESULT        = True
CELERY_TASK_RESULT_EXPIRES  = 60     # seconds (0 means unlimited)
CELERY_MAX_CACHED_RESULTS   = -1     # Disable the cache
CELERY_DISABLE_RATE_LIMITS  = True
CELERYD_MAX_TASKS_PER_CHILD = 1
