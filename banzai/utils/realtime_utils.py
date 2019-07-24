import logging

from banzai import dbs
from banzai.utils.file_utils import get_md5

logger = logging.getLogger('banzai')


def set_file_as_processed(path, db_address=dbs._DEFAULT_DB):
    image = dbs.get_processed_image(path, db_address=db_address)
    if image is not None:
        image.success = True
        dbs.commit_processed_image(image, db_address=db_address)


def increment_try_number(path, db_address=dbs._DEFAULT_DB):
    image = dbs.get_processed_image(path, db_address=db_address)
    # Otherwise increment the number of tries
    image.tries += 1
    dbs.commit_processed_image(image, db_address=db_address)


def file_is_processed(path, db_address, max_tries=5):
    """
    Check if the image has not been marked as processed (which is whether image.success is True)
    :param: max_tries: int
            Maximum number of retries to reduce an image
    :return: 
    """
    image_record = dbs.get_processed_image(path, db_address=db_address)
    processed = True
    if image_record.tries < max_tries and not image_record.success:
        processed = False
        dbs.commit_processed_image(image_record, db_address)
    return processed


def file_changed_on_disc(path, db_address):
    changed = False
    image_record = dbs.get_processed_image(path, db_address=db_address)
    checksum = get_md5(path)
    if image_record.checksum != checksum:
        changed = True
    return changed


def reset_tries(path, db_address):
    image_record = dbs.get_processed_image(path, db_address=db_address)
    image_record.checksum = get_md5(path)
    image_record.tries = 0
    image_record.success = False
    dbs.commit_processed_image(image_record, db_address)