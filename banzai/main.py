""" main.py: Main driver script for banzai.

    The main() function is a console entry point.

Author
    Curtis McCully (cmccully@lcogt.net)

October 2015
"""
import argparse
import os
import logging
import sys
import dramatiq
import json

from datetime import datetime, timedelta
from kombu import Exchange, Connection, Queue
from kombu.mixins import ConsumerMixin
from lcogt_logging import LCOGTFormatter
from dramatiq.brokers.redis import RedisBroker

from banzai import dbs, realtime, logs
from banzai.context import Context, ContextJSONEncoder
from banzai.utils import image_utils, date_utils, fits_utils, instrument_utils, import_utils, lake_utils
from banzai.utils.image_utils import read_image
from banzai import settings


# Logger set up
logging.captureWarnings(True)
# Set up the root logger
root_logger = logging.getLogger()
root_handler = logging.StreamHandler(sys.stdout)
# Add handler
formatter = LCOGTFormatter()
root_handler.setFormatter(formatter)
root_handler.setLevel(getattr(logging, 'DEBUG'))
root_logger.addHandler(root_handler)


logger = logging.getLogger(__name__)

redis_broker = RedisBroker(host=os.getenv('REDIS_HOST', '127.0.0.1'))
dramatiq.set_broker(redis_broker)
dramatiq.set_encoder(ContextJSONEncoder())

RAW_PATH_CONSOLE_ARGUMENT = {'args': ["--raw-path"],
                             'kwargs': {'dest': 'raw_path', 'default': '/archive/engineering',
                                        'help': 'Top level directory where the raw data is stored'}}


def get_stages_todo(ordered_stages, last_stage=None, extra_stages=None):
    """

    Parameters
    ----------
    ordered_stages: list of banzai.stages.Stage objects
    last_stage: banzai.stages.Stage
                Last stage to do
    extra_stages: Stages to do after the last stage

    Returns
    -------
    stages_todo: list of banzai.stages.Stage
                 The stages that need to be done

    Notes
    -----
    Extra stages can be other stages that are not in the ordered_stages list.
    """
    if extra_stages is None:
        extra_stages = []

    if last_stage is None:
        last_index = None
    else:
        last_index = ordered_stages.index(last_stage) + 1

    stages_todo = [import_utils.import_attribute(stage) for stage in ordered_stages[:last_index]]

    stages_todo += [import_utils.import_attribute(stage) for stage in extra_stages]

    return stages_todo


def parse_args(extra_console_arguments=None, parser_description='Process LCO data.'):
    """Parse arguments, including default command line argument, and set the overall log level"""

    parser = argparse.ArgumentParser(description=parser_description)

    parser.add_argument("--processed-path", default='/archive/engineering',
                        help='Top level directory where the processed data will be stored')
    parser.add_argument("--log-level", default='debug', choices=['debug', 'info', 'warning',
                                                                 'critical', 'fatal', 'error'])
    parser.add_argument('--post-to-archive', dest='post_to_archive', action='store_true',
                        default=False)
    parser.add_argument('--post-to-elasticsearch', dest='post_to_elasticsearch', action='store_true',
                        default=False)
    parser.add_argument('--fpack', dest='fpack', action='store_true', default=True,
                        help='Fpack the output files?')
    parser.add_argument('--rlevel', dest='rlevel', default=91, type=int, help='Reduction level')
    parser.add_argument('--db-address', dest='db_address',
                        default='mysql://cmccully:password@localhost/test',
                        help='Database address: Should be in SQLAlchemy form')
    parser.add_argument('--elasticsearch-url', dest='elasticsearch_url',
                        default='http://elasticsearch.lco.gtn:9200')
    parser.add_argument('--es-index', dest='elasticsearch_qc_index', default='banzai_qc',
                        help='ElasticSearch index to use for QC results')
    parser.add_argument('--es-doc-type', dest='elasticsearch_doc_type', default='qc',
                        help='Elasticsearch document type for QC records')
    parser.add_argument('--no-bpm', dest='no_bpm', default=False, action='store_true',
                        help='Do not use a bad pixel mask to reduce data (BPM contains all zeros)')
    parser.add_argument('--ignore-schedulability', dest='ignore_schedulability',
                        default=False, action='store_true',
                        help='Relax requirement that the instrument be schedulable')
    parser.add_argument('--use-older-calibrations', dest='use_older_calibrations', default=True, type=bool,
                        help='Only use calibrations that were created before the start of the block?')
    parser.add_argument('--preview-mode', dest='preview_mode', default=False,
                        help='Save the reductions to the preview directory')
    parser.add_argument('--max-tries', dest='max_tries', default=5,
                        help='Maximum number of times to try to process a frame')

    if extra_console_arguments is None:
        extra_console_arguments = []
    for argument in extra_console_arguments:
        parser.add_argument(*argument['args'], **argument['kwargs'])
    args = parser.parse_args()

    logs.set_log_level(args.log_level)

    runtime_context = Context(args)

    return runtime_context


def run(image_path, runtime_context):
    """
    Main driver script for banzai.
    """
    image = read_image(image_path, runtime_context)
    stages_to_do = get_stages_todo(settings.ORDERED_STAGES,
                                   last_stage=settings.LAST_STAGE[image.obstype],
                                   extra_stages=settings.EXTRA_STAGES[image.obstype])
    logger.info("Starting to reduce frame", image=image)
    for stage in stages_to_do:
        stage_to_run = stage(runtime_context)
        image = stage_to_run.run(image)
    if image is None:
        logger.error('Reduction stopped', extra_tags={'filename': image_path})
        return
    image.write(runtime_context)
    logger.info("Finished reducing frame", image=image)


def run_master_maker(image_path_list, runtime_context, frame_type):
    images = [read_image(image_path, runtime_context) for image_path in image_path_list]
    stage_constructor = import_utils.import_attribute(settings.CALIBRATION_STACKER_STAGE[frame_type.upper()])
    stage_to_run = stage_constructor(runtime_context)
    images = stage_to_run.run(images)
    for image in images:
        image.write(runtime_context)


def process_directory(runtime_context, raw_path, image_types=None, log_message=''):
    if len(log_message) > 0:
        logger.info(log_message, extra_tags={'raw_path': raw_path})
    image_path_list = image_utils.make_image_path_list(raw_path)
    if image_types is None:
        image_types = [None]
    images_to_reduce = []
    for image_type in image_types:
        images_to_reduce += image_utils.select_images(image_path_list, runtime_context.db_address, image_type)
    for image_path in images_to_reduce:
        try:
            run(image_path, runtime_context)
        except Exception:
            logger.error(logs.format_exception(), extra_tags={'filename': image_path})


def process_single_frame(runtime_context, raw_path, filename, log_message=''):
    if len(log_message) > 0:
        logger.info(log_message, extra_tags={'raw_path': raw_path, 'filename': filename})
    full_path = os.path.join(raw_path, filename)
    # Short circuit
    if not image_utils.image_can_be_processed(fits_utils.get_primary_header(full_path), runtime_context.db_address):
        logger.error('Image cannot be processed. Check to make sure the instrument '
                     'is in the database and that the OBSTYPE is recognized by BANZAI',
                     extra_tags={'raw_path': raw_path, 'filename': filename})
        return
    try:
        run(full_path, runtime_context)
    except Exception:
        logger.error(logs.format_exception(), extra_tags={'filename': filename})


@dramatiq.actor()
def process_master_maker(runtime_context, instrument, frame_type, min_date, max_date, use_masters=False):
    extra_tags = {'type': instrument.type, 'site': instrument.site,
                  'enclosure': instrument.enclosure, 'telescope': instrument.telescope,
                  'camera': instrument.camera, 'obstype': frame_type,
                  'min_date': min_date.strftime(date_utils.TIMESTAMP_FORMAT),
                  'max_date': max_date.strftime(date_utils.TIMESTAMP_FORMAT)}
    logger.info("Making master frames", extra_tags=extra_tags)
    image_path_list = dbs.get_individual_calibration_images(instrument, frame_type, min_date, max_date,
                                                            use_masters=use_masters,
                                                            db_address=runtime_context.db_address)
    if len(image_path_list) == 0:
        logger.info("No calibration frames found to stack", extra_tags=extra_tags)

    try:
        run_master_maker(image_path_list, runtime_context, frame_type)
    except Exception:
        logger.error(logs.format_exception())
    logger.info("Finished")


def parse_directory_args(runtime_context=None, raw_path=None, extra_console_arguments=None):
    if extra_console_arguments is None:
        extra_console_arguments = []

    if runtime_context is None:
        if raw_path is None:
            extra_console_arguments += [RAW_PATH_CONSOLE_ARGUMENT]

            runtime_context = parse_args(extra_console_arguments=extra_console_arguments)

        if raw_path is None:
            raw_path = runtime_context.raw_path
    return runtime_context, raw_path


def reduce_directory(runtime_context=None, raw_path=None, image_types=None):
    # TODO: Remove image_types once reduce_night is not needed
    runtime_context, raw_path = parse_directory_args(runtime_context, raw_path)
    process_directory(runtime_context, raw_path, image_types=image_types,
                      log_message='Reducing all frames in directory')


def reduce_single_frame(runtime_context=None):
    extra_console_arguments = [{'args': ['--filename'],
                                'kwargs': {'dest': 'filename', 'help': 'Name of file to process'}}]
    runtime_context, raw_path = parse_directory_args(runtime_context, extra_console_arguments=extra_console_arguments)
    process_single_frame(runtime_context, raw_path, runtime_context.filename)


def schedule_calibration_stacking(runtime_context=None, raw_path=None):
    extra_console_arguments = [{'args': ['--site'],
                                'kwargs': {'dest': 'site', 'help': 'Site code (e.g. ogg)', 'required': True}},
                               {'args': ['--enclosure'],
                                'kwargs': {'dest': 'enclosure', 'help': 'Enclosure code (e.g. clma)', 'required': True}},
                               {'args': ['--telescope'],
                                'kwargs': {'dest': 'telescope', 'help': 'Telescope code (e.g. 0m4a)', 'required': True}},
                               {'args': ['--camera'],
                                'kwargs': {'dest': 'camera', 'help': 'Camera (e.g. kb95)', 'required': True}},
                               {'args': ['--frame-type'],
                                'kwargs': {'dest': 'frame_type', 'help': 'Type of frames to process',
                                           'choices': ['bias', 'dark', 'skyflat'], 'required': True}},
                               {'args': ['--min-date'],
                                'kwargs': {'dest': 'min_date', 'required': True, 'type': date_utils.valid_date,
                                           'help': 'Earliest observation time of the individual calibration frames. '
                                                   'Must be in the format "YYYY-MM-DDThh:mm:ss".'}},
                               {'args': ['--max-date'],
                                'kwargs': {'dest': 'max_date', 'required': True, 'type': date_utils.valid_date,
                                           'help': 'Latest observation time of the individual calibration frames. '
                                                   'Must be in the format "YYYY-MM-DDThh:mm:ss".'}}]

    runtime_context, raw_path = parse_directory_args(runtime_context, raw_path,
                                                    extra_console_arguments=extra_console_arguments)
    logger.info('Begin scheduling calibration stacking for {0} to {1}'.format(runtime_context.min_date, runtime_context.max_date))
    schedule_stacking_checks(runtime_context)


def run_realtime_pipeline():
    extra_console_arguments = [{'args': ['--n-processes'],
                                'kwargs': {'dest': 'n_processes', 'default': 12,
                                           'help': 'Number of listener processes to spawn.', 'type': int}},
                               {'args': ['--broker-url'],
                                'kwargs': {'dest': 'broker_url', 'default': '127.0.0.1',
                                           'help': 'URL for the broker service.'}},
                               {'args': ['--queue-name'],
                                'kwargs': {'dest': 'queue_name', 'default': 'banzai_pipeline',
                                           'help': 'Name of the queue to listen to from the fits exchange.'}}]

    runtime_context = parse_args(parser_description='Reduce LCO imaging data in real time.',
                                 extra_console_arguments=extra_console_arguments)

    # Need to keep the amqp logger level at least as high as INFO,
    # or else it send heartbeat check messages every second
    logging.getLogger('amqp').setLevel(max(logger.level, getattr(logging, 'INFO')))

    try:
        dbs.populate_instrument_tables(db_address=runtime_context.db_address)
    except Exception:
        logger.error('Could not connect to the configdb: {error}'.format(error=logs.format_exception()))

    logger.info('Starting pipeline listener')

    fits_exchange = Exchange('fits_files', type='fanout')
    listener = RealtimeModeListener(runtime_context.broker_url, runtime_context)

    with Connection(listener.broker_url) as connection:
        listener.connection = connection.clone()
        listener.queue = Queue(runtime_context.queue_name, fits_exchange)
        try:
            listener.run()
        except listener.connection.connection_errors:
            listener.connection = connection.clone()
            listener.ensure_connection(max_retries=10)
        except KeyboardInterrupt:
            logger.info('Shutting down pipeline listener.')


class RealtimeModeListener(ConsumerMixin):
    def __init__(self, broker_url, runtime_context):
        self.broker_url = broker_url
        self.runtime_context = runtime_context

    def on_connection_error(self, exc, interval):
        logger.error("{0}. Retrying connection in {1} seconds...".format(exc, interval))
        self.connection = self.connection.clone()
        self.connection.ensure_connection(max_retries=10)

    def get_consumers(self, Consumer, channel):
        consumer = Consumer(queues=[self.queue], callbacks=[self.on_message])
        # Only fetch one thing off the queue at a time
        consumer.qos(prefetch_count=1)
        return [consumer]

    def on_message(self, body, message):
        path = body.get('path')
        process_image.send(path, self.runtime_context._asdict())
        message.ack()  # acknowledge to the sender we got this message (it can be popped)


@dramatiq.actor(queue_name=settings.REDIS_QUEUE_NAMES['PROCESS_IMAGE'])
def process_image(path, runtime_context_dict):
    logger.info('Got into actor.')
    runtime_context = Context(runtime_context_dict)
    try:
        # pipeline_context = PipelineContext.from_dict(pipeline_context_json)
        if realtime.need_to_process_image(path, runtime_context,
                                          db_address=runtime_context.db_address,
                                          max_tries=runtime_context.max_tries):
            logger.info('Reducing frame', extra_tags={'filename': os.path.basename(path)})

            # Increment the number of tries for this file
            realtime.increment_try_number(path, db_address=runtime_context.db_address)

            run(path, runtime_context)
            realtime.set_file_as_processed(path, db_address=runtime_context.db_address)

    except Exception:
        logger.error("Exception processing frame: {error}".format(error=logs.format_exception()),
                     extra_tags={'filename': os.path.basename(path)})


def mark_frame(mark_as):
    parser = argparse.ArgumentParser(description="Set the is_bad flag to mark the frame as {mark_as}"
                                                 "for a calibration frame in the database ".format(mark_as=mark_as))
    parser.add_argument('--filename', dest='filename', required=True,
                        help='Name of calibration file to be marked')
    parser.add_argument('--db-address', dest='db_address',
                        default='mysql://cmccully:password@localhost/test',
                        help='Database address: Should be in SQLAlchemy form')
    parser.add_argument("--log-level", default='debug', choices=['debug', 'info', 'warning',
                                                                 'critical', 'fatal', 'error'])

    args = parser.parse_args()
    logs.set_log_level(args.log_level)

    logger.info("Marking the frame {filename} as {mark_as}".format(filename=args.filename, mark_as=mark_as))
    dbs.mark_frame(args.filename, mark_as, db_address=args.db_address)
    logger.info("Finished")


def add_instrument():
    parser = argparse.ArgumentParser(description="Add a new instrument to the database")
    parser.add_argument("--site", help='Site code (e.g. ogg)', required=True)
    parser.add_argument('--enclosure', help= 'Enclosure code (e.g. clma)', required=True)
    parser.add_argument('--telescope', help='Telescope code (e.g. 0m4a)', required=True)
    parser.add_argument("--camera", help='Camera (e.g. kb95)', required=True)
    parser.add_argument("--camera-type", dest='camera_type',
                        help="Camera type (e.g. 1m0-SciCam-Sinistro)", required=True)
    parser.add_argument("--schedulable", help="Mark the instrument as schedulable", action='store_true',
                        dest='schedulable', default=False)
    parser.add_argument('--db-address', dest='db_address', default='sqlite:///test.db',
                        help='Database address: Should be in SQLAlchemy format')
    args = parser.parse_args()
    instrument = {'site': args.site,
                  'enclosure': args.enclosure,
                  'telescope': args.telescope,
                  'camera': args.camera,
                  'type': args.camera_type,
                  'schedulable': args.schedulable}
    dbs.add_instrument(instrument, db_address=args.db_address)


def mark_frame_as_good():
    mark_frame("good")


def mark_frame_as_bad():
    mark_frame("bad")


def update_db():
    parser = argparse.ArgumentParser(description="Query the configdb to ensure that the instruments table"
                                                 "has the most up-to-date information")

    parser.add_argument("--log-level", default='debug', choices=['debug', 'info', 'warning',
                                                                 'critical', 'fatal', 'error'])
    parser.add_argument('--db-address', dest='db_address',
                        default='mysql://cmccully:password@localhost/test',
                        help='Database address: Should be in SQLAlchemy form')
    args = parser.parse_args()
    logs.set_log_level(args.log_level)

    try:
        dbs.populate_instrument_tables(db_address=args.db_address)
    except Exception:
        logger.error('Could not populate instruments table: {error}'.format(error=logs.format_exception()))


RETRY_DELAY = int(os.getenv('RETRY_DELAY', 1000*60*10))


@dramatiq.actor(queue_name=settings.REDIS_QUEUE_NAMES['SCHEDULE_STACK'])
def should_retry_schedule_stack(retries_so_far, message_data):
    logger.info('entering should_retry_schedule_stack')
    logger.info(message_data)
    if retries_so_far >= 2:
        message_data['process_any_images'] = True
    logger.info(message_data)


@dramatiq.actor(max_retries=3, min_backoff=RETRY_DELAY, max_backoff=RETRY_DELAY, queue_name=settings.REDIS_QUEUE_NAMES['SCHEDULE_STACK'])
def schedule_stack(runtime_context_json, blocks, calibration_type, site, camera, enclosure, telescope, process_any_images=True):
    runtime_context_json['min_date'] = datetime.strptime(runtime_context.min_date, '%Y-%m-%d %H:%M:%S')
    runtime_context_json['max_date'] = datetime.strptime(runtime_context.max_date, '%Y-%m-%d %H:%M:%S')
    runtime_context = Context(runtime_context_json)
    logger.info('entering schedule stack for calibration type {0} from {1} to {2}'.format(
        calibration_type,
        runtime_context.min_date,
        runtime_context.max_date
    ))
    instrument = dbs.query_for_instrument(runtime_context.db_address, site, camera, enclosure, telescope)
    completed_image_count = len(dbs.get_individual_calibration_images(instrument, frame_type,
                                                                      runtime_context.min_date,
                                                                      runtime_context.max_date,
                                                                      use_masters=False,
                                                                      db_address=runtime_context.db_address))
    expected_image_count = 0
    for block in blocks:
        for molecule in block['molecules']:
            if runtime_context.frame_type.upper() == molecule['type']:
                expected_image_count += molecule['exposure_count']
    logger.info('expected image count: {0}'.format(str(expected_image_count)))
    if (expected_image_count < completed_image_count and not process_any_images):
        raise
    else:
        process_master_maker(runtime_context, instrument, calibration_type, runtime_context.min_date,
                             runtime_context.max_date)


# @dramatiq.actor()
def schedule_stacking_checks(runtime_context):
    calibration_blocks = lake_utils.get_calibration_blocks_for_time_range(runtime_context.site,
                                                                          runtime_context.max_date,
                                                                          runtime_context.min_date)
    instruments = dbs.get_instruments_at_site(site=runtime_context.site, db_address=runtime_context.db_address)
    for instrument in instruments:
        blocks_for_calibration = lake_utils.get_calibration_blocks_for_type(instrument,
                                                                            runtime_context.frame_type,
                                                                            calibration_blocks)
        if len(blocks_for_calibration) > 0:
            block_ids = [block['id'] for block in blocks_for_calibration]
            block_end = datetime.strptime(blocks_for_calibration[0]['end'], date_utils.TIMESTAMP_FORMAT)
            stack_delay = timedelta(milliseconds=settings.CALIBRATION_STACK_DELAYS[runtime_context.frame_type.upper()])
            now = datetime.utcnow()
            # message_delay = now - block_end + stack_delay
            logger.info('before send schedule_stack')
            # schedule_stack.send_with_options(args=(runtime_context, block_for_calibration['id'],
            #     calibration_type, instrument), delay=max(message_delay.microseconds*1000, 0))
            logger.info(runtime_context._asdict())
            schedule_stack.send_with_options(args=(runtime_context._asdict(), blocks_for_calibration,
                                                   runtime_context.frame_type, instrument.site,
                                                   instrument.camera, instrument.enclosure, instrument.telescope),
                                             kwargs={'process_any_images', False},
                                             on_failure=should_retry_schedule_stack)
