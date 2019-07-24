import os
from glob import glob
import logging

from banzai import settings
from banzai import logs
from banzai import dbs
from banzai.munge import munge
from banzai.utils.fits_utils import get_primary_header
from banzai.utils.realtime_utils import file_is_processed, file_changed_on_disc, reset_tries
from banzai.utils.instrument_utils import instrument_passes_criteria
from banzai.utils import import_utils
from banzai.exceptions import InhomogeneousSetException


logger = logging.getLogger('banzai')


FRAME_CLASS = import_utils.import_attribute(settings.FRAME_CLASS)


def get_obstype(header):
    return header.get('OBSTYPE', None)


def get_reduction_level(header):
    return header.get('RLEVEL', '00')


def is_master(header):
    return header.get('ISMASTER', False)


def select_images(image_list, image_type, db_address, ignore_schedulability):
    images = []
    for filename in image_list:
        try:
            header = get_primary_header(filename)
            should_process = need_to_process_image(filename, db_address=db_address,
                                                   ignore_schedulability=ignore_schedulability)
            should_process &= (image_type is None or get_obstype(header) == image_type)
            if not ignore_schedulability:
                instrument = dbs.get_instrument(header, db_address=db_address)
                should_process &= instrument.schedulable
            if should_process:
                images.append(filename)
        except Exception:
            logger.error(logs.format_exception(), extra_tags={'filename': filename})
    return images


def make_image_path_list(raw_path):
    if os.path.isdir(raw_path):
        # return the list of file and a dummy image configuration
        fits_files = glob(os.path.join(raw_path, '*.fits'))
        fz_files = glob(os.path.join(raw_path, '*.fits.fz'))

        fz_files_to_remove = []
        for i, f in enumerate(fz_files):
            if f[:-3] in fits_files:
                fz_files_to_remove.append(f)

        for f in fz_files_to_remove:
            fz_files.remove(f)
        image_path_list = fits_files + fz_files

    else:
        image_path_list = glob(raw_path)
    return image_path_list


def check_image_homogeneity(images, group_by_attributes=None):
    attribute_list = ['nx', 'ny', 'site', 'camera']
    if group_by_attributes is not None:
        attribute_list += group_by_attributes
    for attribute in attribute_list:
        if len(set([getattr(image, attribute) for image in images])) > 1:
            raise InhomogeneousSetException('Images have different {0}s'.format(attribute))


def need_to_process_image(path, db_address=dbs._DEFAULT_DB, ignore_schedulability=False, max_tries=5):
    """
    Decides whether we need to process the image located at path.

    Parameters
    ----------
    path: str
          Full path to the image possibly needing to be processed
    ignore_schedulability: bool
             Process non-schedulable instruments
    db_address: str
                SQLAlchemy style URL to the database with the status of previous reductions
    max_tries: int
               Maximum number of retries to reduce an image

    Returns
    -------
    need_to_process: bool
                  True if we should try to process the image

    Notes
    -----
    If the file has changed on disk, we reset the success flags and the number of tries to zero.
    We only attempt to make images if the instrument is in the database and passes the given criteria.
    """
    process = True
    logger.info("Checking if file needs to be processed", extra_tags={"filename": path})

    if not (path.endswith('.fits') or path.endswith('.fits.fz')):
        logger.info("Filename does not have a .fits extension. Will not process.", extra_tags={"filename": path})
        process = False
    header = get_primary_header(path)

    if header is None:
        logger.info('Header being checked to process image is None. Will not process.')
        process = False
    if not get_obstype(header) in settings.LAST_STAGE:
        logger.info('Image has an obstype that is not supported by banzai. Will not process.')
        process = False
    if not get_reduction_level(header) != '00':
        logger.info('Image has nonzero reduction level. Will not process.')
        process = False

    try:
        instrument = dbs.get_instrument(header, db_address=db_address)
        if not instrument_passes_criteria(instrument, settings.FRAME_SELECTION_CRITERIA):
            logger.info('Instrument does not pass reduction criteria. Will not process.')
            process = False
        if not ignore_schedulability and not instrument.schedulable:
            logger.info('Instrument is not schedulable. Will not process.',
                        extra_tags={"filename": path})
            process = False
    except ValueError:
        logger.info('ValueError while loading Instrument from database. Will not process.')
        process = False

    if file_changed_on_disc(path, db_address):
        logger.info('File has changed on disc, ignoring previous attempts at reduction')
        reset_tries(path, db_address)
    elif file_is_processed(path, db_address, max_tries=max_tries):
        logger.info('File is already processed. Will not process.')
        process = False

    return process


def read_image(filename, runtime_context):
    try:
        image = FRAME_CLASS(runtime_context, filename=filename)
        if image.instrument is None:
            logger.error("Image instrument attribute is None, aborting", image=image)
            raise IOError
        munge(image)
        return image
    except Exception:
        logger.error('Error loading image: {error}'.format(error=logs.format_exception()),
                     extra_tags={'filename': filename})


def get_configuration_mode(header):
    configuration_mode = header.get('CONFMODE', 'default')
    # If the configuration mode is not in the header, fallback to default to support legacy data
    if (
            configuration_mode == 'N/A' or
            configuration_mode == 0 or
            configuration_mode.lower() == 'normal'
    ):
        configuration_mode = 'default'

    header['CONFMODE'] = configuration_mode
    return configuration_mode
