"""Keep private download tickets out of application HTTP access logs."""
import logging


class DownloadTicketFilter(logging.Filter):
    def filter(self, record):
        def redact(value):
            text = str(value)
            if '/downloads/' in text and '?ticket=' in text:
                return text.split('?ticket=', 1)[0] + '?ticket=[redacted]'
            return value
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        record.msg = redact(record.msg)
        return True


def protect_download_logs():
    for name in ('uvicorn.access', 'httpx'):
        logger = logging.getLogger(name)
        if not any(isinstance(item, DownloadTicketFilter) for item in logger.filters):
            logger.addFilter(DownloadTicketFilter())
