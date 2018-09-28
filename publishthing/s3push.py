import os
import threading
import sys

from .core import log

from hashlib import md5

if sys.version_info.major >= 3:
    from queue import Queue
else:
    from Queue import Queue

import boto
import boto.s3.connection


def thread_queue(producer, consumer, num_workers=8):
    workers = []

    queue = Queue()

    for i in range(num_workers):
        worker = threading.Thread(target=consumer, args=[queue])
        worker.start()

        workers.append(worker)

    producer(queue)

    for worker in workers:
        queue.put(None)

    for worker in workers:
        worker.join()


def s3_upload(s3_bucket, lpath):
    def producer(queue):
        for root, dirs, files in os.walk(lpath):
            for lfile in files:
                if os.path.basename(lfile).startswith("."):
                    pass
                else:
                    file = os.path.join(root, lfile).replace(
                        lpath + "/", "", 1)
                    queue.put((os.path.join(root, lfile), file))

    def consumer(queue):
        conn = boto.connect_s3(
            calling_format=boto.s3.connection.OrdinaryCallingFormat())
        bucket = conn.get_bucket(s3_bucket)

        item = queue.get()

        while item:
            source, target = item
            upload = True

            key = bucket.get_key(target)

            if key:
                source_md5 = md5()

                for chunk in open(source):
                    source_md5.update(chunk)

                upload = key.etag[1:-1] != source_md5.hexdigest()
            else:
                key = bucket.new_key(target)

            if upload:
                key.set_contents_from_filename(source,
                                               replace=True,
                                               policy="public-read")

                log("uploaded %s", target)
            else:
                log("unchanged %s", target)
                key.make_public()

            item = queue.get()

    thread_queue(producer, consumer)
