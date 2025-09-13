"""Downloads media from telegram."""
import asyncio
import logging
import os
from typing import List, Optional, Tuple, Union

import pyrogram
import yaml
from pyrogram.types import Audio, Document, Photo, Video, VideoNote, Voice
from pyrogram.errors import BadRequest
from rich.logging import RichHandler

from utils.file_management import get_next_name, manage_duplicate_file
from utils.log import LogFilter
from utils.meta import print_meta
from utils.updates import check_for_updates

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()],
)
logging.getLogger("pyrogram.session.session").addFilter(LogFilter())
logging.getLogger("pyrogram.client").addFilter(LogFilter())
logging.getLogger("pyrogram.dispatcher").addFilter(LogFilter())
logging.getLogger("pyrogram.connection.connection").addFilter(LogFilter())
logger = logging.getLogger("media_downloader")

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# List of other directories to check for existing files (absolute paths)
OTHER_DIRS_TO_CHECK = [
    os.path.expanduser("~/Downloads/Books/New"),
    os.path.expanduser("~/Downloads/Books/New/EPUB"),
    os.path.expanduser("~/Downloads/Books/New/PDF"),
]
FAILED_IDS: list = []
DOWNLOADED_IDS: list = []

DRY_MODE = False

def update_config(config: dict):
    """
    Update existing configuration file.

    Parameters
    ----------
    config: dict
        Configuration to be written into config file.
    """
    config["ids_to_retry"] = (
        list(set(config["ids_to_retry"]) - set(DOWNLOADED_IDS)) + FAILED_IDS
    )
    if not DRY_MODE:
        with open("config.yaml", "w") as yaml_file:
            yaml.dump(config, yaml_file, default_flow_style=False)
    logger.info(f"Updated last read message_id ({config['last_read_message_id']}) to config file") # type: ignore


def _can_download(_type: str, file_formats: dict, file_format: Optional[str]) -> bool:
    """
    Check if the given file format can be downloaded.

    Parameters
    ----------
    _type: str
        Type of media object.
    file_formats: dict
        Dictionary containing the list of file_formats
        to be downloaded for `audio`, `document` & `video`
        media types
    file_format: str
        Format of the current file to be downloaded.

    Returns
    -------
    bool
        True if the file format can be downloaded else False.
    """
    if _type in ["audio", "document", "video"]:
        allowed_formats: list = file_formats[_type]
        if not file_format in allowed_formats and allowed_formats[0] != "all":
            return False
    return True


def _is_exist(file_path: str) -> bool:
    """
    Check if a file exists and it is not a directory.

    Parameters
    ----------
    file_path: str
        Absolute path of the file to be checked.

    Returns
    -------
    bool
        True if the file exists else False.
    """
    return not os.path.isdir(file_path) and os.path.exists(file_path)


async def _get_media_meta(
    media_obj: Union[Audio, Document, Photo, Video, VideoNote, Voice],
    _type: str,
) -> Tuple[str, Optional[str]]:
    """Extract file name and file id from media object.

    Parameters
    ----------
    media_obj: Union[Audio, Document, Photo, Video, VideoNote, Voice]
        Media object to be extracted.
    _type: str
        Type of media object.

    Returns
    -------
    Tuple[str, Optional[str]]
        file_name, file_format
    """
    if _type in ["audio", "document", "video"]:
        # pylint: disable = C0301
        file_format: Optional[str] = media_obj.mime_type.split("/")[-1]  # type: ignore
    else:
        file_format = None

    if _type in ["voice", "video_note"]:
        # pylint: disable = C0209
        file_format = media_obj.mime_type.split("/")[-1]  # type: ignore
        file_name: str = os.path.join(
            THIS_DIR,
            _type,
            "{}_{}.{}".format(
                _type,
                media_obj.date.isoformat(),  # type: ignore
                file_format,
            ),
        )
    else:
        file_name = os.path.join(
            THIS_DIR, _type, getattr(media_obj, "file_name", None) or ""
        )
    return file_name, file_format


async def download_media(
    client: pyrogram.client.Client,
    message: pyrogram.types.Message,
    media_types: List[str],
    file_formats: dict,
):
    """
    Download media from Telegram.

    Each of the files to download are retried 3 times with a
    delay of 5 seconds each.

    Parameters
    ----------
    client: pyrogram.client.Client
        Client to interact with Telegram APIs.
    message: pyrogram.types.Message
        Message object retrieved from telegram.
    media_types: list
        List of strings of media types to be downloaded.
        Ex : `["audio", "photo"]`
        Supported formats:
            * audio
            * document
            * photo
            * video
            * voice
    file_formats: dict
        Dictionary containing the list of file_formats
        to be downloaded for `audio`, `document` & `video`
        media types.

    Returns
    -------
    int
        Current message id.
    """
    for retry in range(3):
        try:
            if message.media is None:
                return message.id
            for _type in media_types:
                _media = getattr(message, _type, None)
                if _media is None:
                    continue
                file_name, file_format = await _get_media_meta(_media, _type)
                file_size = getattr(_media, "file_size", 0) or 0
                if _can_download(_type, file_formats, file_format):
                        candidate_name = file_name
                        counter = 1
                        skip_download = False
                        while True:
                            # Check if already in the download dir or in other dirs
                            check_paths = [candidate_name]
                            for other_dir in OTHER_DIRS_TO_CHECK:
                                check_paths.append(os.path.join(other_dir, os.path.basename(candidate_name)))
                            for check_path in check_paths:
                                if _is_exist(check_path):
                                    existing_size = os.path.getsize(check_path)
                                    if existing_size == file_size:
                                        logger.info("Message[%d]: Skipping %s as file with same name and size already exists in %s", message.id, os.path.basename(candidate_name), os.path.dirname(check_path))
                                        skip_download = True
                                        break
                            if skip_download or not any(_is_exist(p) for p in check_paths):
                                break
                            # If file exists but size is different, try next candidate name
                            base, ext = os.path.splitext(file_name)
                            candidate_name = f"{base}_{counter}{ext}"
                            counter += 1
                        if not skip_download:
                            if not DRY_MODE:
                                download_path = await client.download_media(
                                    message, file_name=candidate_name
                                )
                            else:
                                download_path = candidate_name
                            logger.info("Message[%d]: Media downloaded for message - %s", message.id, download_path)
                        DOWNLOADED_IDS.append(message.id)
            return message.id
        except BadRequest:
            logger.warning(
                "Message[%d]: file reference expired, refetching...",
                message.id,
            )
            if retry < 2:  # Only refetch if we have retries left
                message = await client.get_messages(  # type: ignore
                    chat_id=message.chat.id,  # type: ignore
                    message_ids=message.id,
                )
                continue  # Go to next retry iteration
            else:
                logger.error(
                    "Message[%d]: file reference expired for 3 retries, download skipped.",
                    message.id,
                )
                FAILED_IDS.append(message.id)
                break  # Exit retry loop
        except TypeError:
            logger.warning(
                "Message[%d]: Timeout Error occurred when downloading, retrying after 5 seconds",
                message.id,
            )
            if retry < 2:  # Only sleep if we have retries left
                await asyncio.sleep(5)
                continue  # Go to next retry iteration
            else:
                logger.error(
                    "Message[%d]: Timing out after 3 retries, download skipped.",
                    message.id,
                )
                FAILED_IDS.append(message.id)
                break  # Exit retry loop
        except Exception as e:
            logger.error(
                "Message[%d]: could not be downloaded due to following exception:\n[%s].",
                message.id,
                e,
                exc_info=True,
            )
            if retry < 2:
                logger.info("Message[%d]: Retrying...")
                continue  # Retry for generic exceptions too
            else:
                FAILED_IDS.append(message.id)
                break  # Exit retry loop
    return message.id


async def process_messages(
    client: pyrogram.client.Client,
    messages: List[pyrogram.types.Message],
    media_types: List[str],
    file_formats: dict,
) -> int:
    """
    Download media from Telegram.

    Parameters
    ----------
    client: pyrogram.client.Client
        Client to interact with Telegram APIs.
    messages: list
        List of telegram messages.
    media_types: list
        List of strings of media types to be downloaded.
        Ex : `["audio", "photo"]`
        Supported formats:
            * audio
            * document
            * photo
            * video
            * voice
    file_formats: dict
        Dictionary containing the list of file_formats
        to be downloaded for `audio`, `document` & `video`
        media types.

    Returns
    -------
    int
        Max value of list of message ids.
    """
    message_ids = []
    for message in messages:
        message_id = await download_media(client, message, media_types, file_formats)
        message_ids.append(message_id)

    last_message_id: int = max(message_ids)
    return last_message_id


async def begin_import(config: dict, pagination_limit: int) -> dict:
    """
    Create pyrogram client and initiate download.

    The pyrogram client is created using the ``api_id``, ``api_hash``
    from the config and iter through message offset on the
    ``last_message_id`` and the requested file_formats.

    Parameters
    ----------
    config: dict
        Dict containing the config to create pyrogram client.
    pagination_limit: int
        Number of message to download asynchronously as a batch.

    Returns
    -------
    dict
        Updated configuration to be written into config file.
    """
    client = pyrogram.Client(
        "media_downloader",
        api_id=config["api_id"],
        api_hash=config["api_hash"],
        proxy=config.get("proxy"),
    )
    await client.start()
    last_read_message_id: int = config["last_read_message_id"]
    # Keep track of the highest message id we have processed in this run.
    # Since messages_iter yields from most recent down to min_id, the last
    # processed batch may contain older (smaller) ids. Without tracking the
    # global maximum, we'd mistakenly persist a lower id at the end.
    highest_read_message_id: int = last_read_message_id
    # message[0] = last message (id = 65328)
    messages_iter = client.get_chat_history(
        config["chat_id"], min_id=last_read_message_id
    )
    messages_list: list = []
    pagination_count: int = 0
    if config["ids_to_retry"]:
        logger.info("Downloading files failed during last run...")
        skipped_messages: list = await client.get_messages(  # type: ignore
            chat_id=config["chat_id"], message_ids=config["ids_to_retry"]
        )
        for message in skipped_messages:
            pagination_count += 1
            messages_list.append(message)

    async for message in messages_iter:  # type: ignore
        if pagination_count != pagination_limit:
            pagination_count += 1
            messages_list.append(message)
        else:
            batch_max_message_id = await process_messages(
                client,
                messages_list,
                config["media_types"],
                config["file_formats"],
            )
            # Update the highest read message id seen so far
            highest_read_message_id = max(highest_read_message_id, batch_max_message_id)
            pagination_count = 0
            messages_list = []
            messages_list.append(message)
            config["last_read_message_id"] = highest_read_message_id
            update_config(config)
    if messages_list:
        batch_max_message_id = await process_messages(
            client,
            messages_list,
            config["media_types"],
            config["file_formats"],
        )
        highest_read_message_id = max(highest_read_message_id, batch_max_message_id)

    await client.stop()
    config["last_read_message_id"] = highest_read_message_id
    return config


def main():
    """Main function of the downloader."""

    if DRY_MODE:
        logger.info("** Running in dry mode! **")

    with open(os.path.join(THIS_DIR, "config.yaml")) as f:
        config = yaml.safe_load(f)
    updated_config = asyncio.get_event_loop().run_until_complete(
        begin_import(config, pagination_limit=100)
    )
    if FAILED_IDS:
        logger.info(
            "Downloading of %d files failed. "
            "Failed message ids are added to config file.\n"
            "These files will be downloaded on the next run.",
            len(set(FAILED_IDS)),
        )
    update_config(updated_config)
    check_for_updates()


if __name__ == "__main__":
    print_meta(logger)
    main()
