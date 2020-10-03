#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""
A simple tool to sync an iCloud drive to a local folder
Based on https://github.com/picklepete/pyicloud/blob/master/pyicloud/cmdline.py
"""
from __future__ import print_function
from builtins import input
import traceback
import argparse
import pickle
import time
import sys
import os

from click import confirm
from shutil import copyfileobj

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudFailedLoginException, PyiCloudAPIResponseException
from . import utils

verbose = False
silent = False

def create_pickled_data(idevice, filename):
    """
    This helper will output the idevice to a pickled file named
    after the passed filename.

    This allows the data to be used without resorting to screen / pipe
    scrapping.
    """
    pickle_file = open(filename, "wb")
    pickle.dump(idevice.content, pickle_file, protocol=pickle.HIGHEST_PROTOCOL)
    pickle_file.close()

def sync_folder(drive, destination, items):
    for i in items:
        item=drive[i]
        if item.type == 'folder':
            newdir = os.path.join(destination, item.name)
            os.makedirs(newdir, exist_ok=True)
            sync_folder(item, newdir, item.dir())
        elif item.type == 'file':
            localfile = os.path.join(destination, item.name)
            if os.path.isfile(localfile):
                localtime = int(os.path.getmtime(localfile))
                remotetime = int(item.date_modified.timestamp())
                localsize = os.path.getsize(localfile)
                remotesize = item.size
                if localtime == remotetime and localsize == remotesize:
                    if verbose:
                        print("Skipping already downloaded file {}".format(localfile))
                    continue
            if verbose:
                print("Downloading {} to {}".format(item.name, destination))
            try:
                with item.open(stream=True) as response:
                    with open(localfile, 'wb') as file_out:
                        copyfileobj(response.raw, file_out)
                modtime = time.mktime(item.date_modified.timetuple())
                os.utime(localfile, (modtime, modtime))
            except PyiCloudAPIResponseException as e:
                if not silent:
                    print("Failed to download {}: {}".format(localfile, str(e)))

def main(args=None):
    """Main commandline entrypoint."""
    global verbose
    global silent

    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser(description="iCloud Drive Sync Tool")

    parser.add_argument(
        "-u",
        "--username",
        action="store",
        dest="username",
        default="",
        help="Apple ID to Use",
    )
    parser.add_argument(
        "-p",
        "--password",
        action="store",
        dest="password",
        default="",
        help=(
            "Apple ID Password to Use; if unspecified, password will be "
            "fetched from the system keyring."
        ),
    )
    parser.add_argument(
        "-e",
        "--environment",
        action="store_true",
        dest="useEnvironment",
        default=False,
        help="Read USERNAME and PASSWORD from environment variables.",
    )
    parser.add_argument(
        "-n",
        "--non-interactive",
        action="store_false",
        dest="interactive",
        default=True,
        help="Disable interactive prompts.",
    )
    parser.add_argument(
        "--delete-from-keyring",
        action="store_true",
        dest="delete_from_keyring",
        default=False,
        help="Delete stored password in system keyring for this username.",
    )
    parser.add_argument(
        "-d",
        "--destination",
        action="store",
        dest="destination",
        default=False,
        required=True,
        help="Destination directory for files downloaded from the iCloud Drive",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        dest="verbose",
        default=False,
        help="Print verbose status messages while working",
    )
    parser.add_argument(
        "-s",
        "--silent",
        action="store_true",
        dest="silent",
        default=False,
        help="Silently ignore api errors except authentication issues.",
    )

    command_line = parser.parse_args(args)

    verbose = command_line.verbose
    silent = command_line.silent

    if command_line.useEnvironment:
        username = os.environ.get('USERNAME')
        password = os.environ.get('PASSWORD')
    else:
        username = command_line.username
        password = command_line.password

    if username and command_line.delete_from_keyring:
        utils.delete_password_in_keyring(username)

    failure_count = 0
    while True:
        # Which password we use is determined by your username, so we
        # do need to check for this first and separately.
        if not username:
            parser.error("No username supplied")

        if not password:
            password = utils.get_password(
                username, interactive=command_line.interactive
            )

        if not password:
            parser.error("No password supplied")

        try:
            api = PyiCloudService(username.strip(), password.strip())
            if (
                not utils.password_exists_in_keyring(username)
                and command_line.interactive
                and confirm("Save password in keyring?")
            ):
                utils.store_password_in_keyring(username, password)

            if api.requires_2sa:
                # fmt: off
                print(
                    "\nTwo-step authentication required.",
                    "\nYour trusted devices are:"
                )
                # fmt: on

                devices = api.trusted_devices
                for i, device in enumerate(devices):
                    print(
                        "    %s: %s"
                        % (
                            i,
                            device.get(
                                "deviceName", "SMS to %s" % device.get("phoneNumber")
                            ),
                        )
                    )

                print("\nWhich device would you like to use?")
                if command_line.interactive:
                    device = int(input("(number) --> "))
                    device = devices[device]
                    if not api.send_verification_code(device):
                        print("Failed to send verification code")
                        sys.exit(1)

                    print("\nPlease enter validation code")
                    code = input("(string) --> ")
                    if not api.validate_verification_code(device, code):
                        print("Failed to verify verification code")
                        sys.exit(1)
                    print("")
                else:
                    print("2 factor authentication required while in non-interactive mode.")
                    print("Please run this script without the -n switch.")
                    sys.exit(1)
            break
        except PyiCloudFailedLoginException:
            # If they have a stored password; we just used it and
            # it did not work; let's delete it if there is one.
            if utils.password_exists_in_keyring(username):
                utils.delete_password_in_keyring(username)

            message = "Bad username or password for {username}".format(
                username=username,
            )
            password = None

            failure_count += 1
            if failure_count >= 3:
                raise RuntimeError(message)

            print(message, file=sys.stderr)

    try:
        sync_folder(api.drive, command_line.destination, api.drive.dir())
    except Exception as e:
        if not silent:
            traceback.print_exc()

if __name__ == "__main__":
    main()
