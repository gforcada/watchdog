#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2011 Yesudeep Mangalapilly <yesudeep@gmail.com>
# Copyright 2012 Google, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from __future__ import with_statement

import ctypes
import threading
import os.path
import time

from pathtools.path import absolute_path

from watchdog.observers.winapi import (
    read_directory_changes,
    get_directory_handle,
    close_directory_handle,
)

from watchdog.observers.winapi import (
    FILE_ACTION_CREATED,
    FILE_ACTION_DELETED,
    FILE_ACTION_MODIFIED,
    FILE_ACTION_RENAMED_OLD_NAME,
    FILE_ACTION_RENAMED_NEW_NAME,
    get_FILE_NOTIFY_INFORMATION
)

from watchdog.observers.api import (
    EventEmitter,
    BaseObserver,
    DEFAULT_OBSERVER_TIMEOUT,
    DEFAULT_EMITTER_TIMEOUT
)

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    DirModifiedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    FileModifiedEvent,
    generate_sub_moved_events,
    generate_sub_created_events,
)


DIR_ACTION_EVENT_MAP = {
    FILE_ACTION_CREATED: DirCreatedEvent,
    FILE_ACTION_DELETED: DirDeletedEvent,
    FILE_ACTION_MODIFIED: DirModifiedEvent,
}
FILE_ACTION_EVENT_MAP = {
    FILE_ACTION_CREATED: FileCreatedEvent,
    FILE_ACTION_DELETED: FileDeletedEvent,
    FILE_ACTION_MODIFIED: FileModifiedEvent,
}

# HACK:
WATCHDOG_TRAVERSE_MOVED_DIR_DELAY = 1   # seconds

BUFFER_SIZE = 2048


class WindowsApiEmitter(EventEmitter):

    """
    Windows API-based emitter that uses ReadDirectoryChangesW
    to detect file system changes for a watch.
    """

    def __init__(self, event_queue, watch, timeout=DEFAULT_EMITTER_TIMEOUT):
        EventEmitter.__init__(self, event_queue, watch, timeout)
        self._lock = threading.Lock()

        self._directory_handle = get_directory_handle(watch.path)
        self._buffer = ctypes.create_string_buffer(BUFFER_SIZE)

    def on_thread_stop(self):
        close_directory_handle(self._directory_handle)

    def queue_events(self, timeout):
        with self._lock:
            dir_changes, nbytes = read_directory_changes(
                self._directory_handle,
                self._buffer,
                self.watch.is_recursive
            )

            last_renamed_src_path = ""
            for action, src_path in get_FILE_NOTIFY_INFORMATION(dir_changes, nbytes):
                src_path = absolute_path(os.path.join(self.watch.path,
                                                      src_path))

                if action == FILE_ACTION_RENAMED_OLD_NAME:
                    last_renamed_src_path = src_path
                elif action == FILE_ACTION_RENAMED_NEW_NAME:
                    dest_path = src_path
                    src_path = last_renamed_src_path

                    if os.path.isdir(dest_path):
                        event = DirMovedEvent(src_path, dest_path)
                        if self.watch.is_recursive:
                            # HACK: We introduce a forced delay before
                            # traversing the moved directory. This will read
                            # only file movement that finishes within this
                            # delay time.
                            time.sleep(WATCHDOG_TRAVERSE_MOVED_DIR_DELAY)
                            # The following block of code may not
                            # obtain moved events for the entire tree if
                            # the I/O is not completed within the above
                            # delay time. So, it's not guaranteed to work.
                            # TODO: Come up with a better solution, possibly
                            # a way to wait for I/O to complete before
                            # queuing events.
                            for sub_moved_event in generate_sub_moved_events(src_path, dest_path):
                                self.queue_event(sub_moved_event)
                        self.queue_event(event)
                    else:
                        self.queue_event(FileMovedEvent(src_path, dest_path))
                else:
                    if os.path.isdir(src_path):
                        event = DIR_ACTION_EVENT_MAP[action](src_path)
                        if isinstance(event, DirCreatedEvent):
                            # If a directory is moved from outside the watched folder to inside it
                            # we only get a created directory event out of it, not any events for its children
                            # so use the same hack as for file moves to get the child events
                            time.sleep(WATCHDOG_TRAVERSE_MOVED_DIR_DELAY)
                            sub_events = generate_sub_created_events(src_path)
                            for sub_created_event in sub_events:
                                self.queue_event(sub_created_event)
                        self.queue_event(event)
                    else:
                        self.queue_event(FILE_ACTION_EVENT_MAP[action](src_path))


class WindowsApiObserver(BaseObserver):

    """
    Observer thread that schedules watching directories and dispatches
    calls to event handlers.
    """

    def __init__(self, timeout=DEFAULT_OBSERVER_TIMEOUT):
        BaseObserver.__init__(self,
                              emitter_class=WindowsApiEmitter,
                              timeout=timeout)
