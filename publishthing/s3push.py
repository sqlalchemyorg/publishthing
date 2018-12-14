from hashlib import md5
import os
from queue import Queue
import threading
from typing import Callable, IO, Any, Tuple, AnyStr, Optional
import boto
import boto.s3.connection

from . import publishthing  # noqa

_WorkQueueItem = Optional[Tuple[str, str]]

def s3_upload(
        thing: "publishthing.PublishThing",
        s3_bucket: str, lpath: str) -> None:

    def producer(queue: "Queue[_WorkQueueItem]") -> None:
        for root, dirs, files in os.walk(lpath):
            for lfile in files:
                if os.path.basename(lfile).startswith("."):
                    pass
                else:
                    file = os.path.join(root, lfile).replace(
                        lpath + "/", "", 1)
                    queue.put((os.path.join(root, lfile), file))

    def consumer(queue: "Queue[_WorkQueueItem]") -> None:
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

                for chunk in open(source, 'rb'):
                    source_md5.update(chunk)

                upload = key.etag[1:-1] != source_md5.hexdigest()
            else:
                key = bucket.new_key(target)

            if upload:
                key.set_contents_from_filename(source,
                                               replace=True,
                                               policy="public-read")

                thing.message("uploaded %s", target)
            else:
                thing.message("unchanged %s", target)
                key.make_public()

            item = queue.get()

    _thread_queue(producer, consumer)



def _thread_queue(
        producer: Callable[["Queue[_WorkQueueItem]"], None],
        consumer: Callable[["Queue[_WorkQueueItem]"], None],
        num_workers: int=8) -> None:
    workers = []

    queue: "Queue[_WorkQueueItem]" = Queue()

    for i in range(num_workers):
        worker = threading.Thread(target=consumer, args=[queue])
        worker.start()

        workers.append(worker)

    producer(queue)

    # indicate to the workers to exit.
    # should maybe use a threading.Event here.
    for worker in workers:
        queue.put(None)

    for worker in workers:
        worker.join()


