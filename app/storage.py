"""Choose durable Firestore in Cloud Run; retain SQLite for local development."""
import functools
import os


def is_cloud():
    return os.environ.get("STORAGE_BACKEND") == "firestore"


def cloud(prefix):
    def decorate(fn):
        @functools.wraps(fn)
        def dispatch(*args, **kwargs):
            if is_cloud():
                from . import cloudstore
                return getattr(cloudstore, prefix + "_" + fn.__name__)(*args, **kwargs)
            return fn(*args, **kwargs)
        return dispatch
    return decorate
