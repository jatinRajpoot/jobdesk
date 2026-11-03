"""Deployment helpers. Secret values only travel through process memory/stdin."""
import shutil
import subprocess

PROJECT = "analog-height-492914-t1"
REGION = "asia-south1"
GCLOUD = shutil.which("gcloud.cmd") or shutil.which("gcloud")


def gcloud(*args, input_bytes=None, check=True):
    result = subprocess.run([GCLOUD, *args, "--project=" + PROJECT, "--quiet"], input=input_bytes, capture_output=True)
    if check and result.returncode:
        raise RuntimeError("gcloud " + " ".join(args[:3]) + " failed: " + result.stderr.decode(errors="replace")[-1500:])
    return result


def google_credentials():
    from google.oauth2.credentials import Credentials
    result = gcloud("auth", "print-access-token")
    return Credentials(token=result.stdout.decode().strip())


def secret_value(name):
    return gcloud("secrets", "versions", "access", "latest", "--secret=" + name).stdout


def ensure_secret(name, value=None):
    result = gcloud("secrets", "describe", name, check=False)
    if result.returncode:
        if value is None:
            raise ValueError("No value available for new secret " + name)
        gcloud("secrets", "create", name, "--replication-policy=automatic")
        gcloud("secrets", "versions", "add", name, "--data-file=-", input_bytes=value)
        print("Created secret: " + name, flush=True)
    elif value is not None and secret_value(name) != value:
        gcloud("secrets", "versions", "add", name, "--data-file=-", input_bytes=value)
        print("Updated secret: " + name, flush=True)
    else:
        print("Reusing secret: " + name, flush=True)
