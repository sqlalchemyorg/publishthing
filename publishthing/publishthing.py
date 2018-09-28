#!/usr/bin/python
import argparse
import os
from subprocess import check_call
from .core import log, git_checkout_files, hg_checkout_files
from . import s3push


def blogofile_build(checkout):
    log("building with blogofile")
    log("base dir %s", checkout)
    os.chdir(checkout)
    check_call(["blogofile", "build"])
    return os.path.join(checkout, "_site")


def zeekofile_build(checkout):
    log("building with zeekofile")
    log("base dir %s", checkout)
    os.chdir(checkout)
    check_call(["zeekofile", "build"])
    return os.path.join(checkout, "_site")


def publish_local(copy_from, sitename, local_base, local_prefix, dry):
    site_location = os.path.join(local_base, sitename)
    if not os.path.exists(site_location):
        raise Exception("No such site: %s" % site_location)

    dest = os.path.join(site_location, local_prefix)
    log(
        "%sCopying %s to %s",
        "(dry) " if dry else "",
        copy_from,
        dest)
    if not dry:
        check_call(["bash", "-c", "cp -R %s/* %s" % (copy_from, dest)])


def publish_s3(copy_from, sitename, dry):
    log("%sPublishing %s to S3 bucket %s",
        "(dry) " if dry else "",
        copy_from,
        sitename)
    if not dry:
        s3push.s3_upload(sitename, copy_from)


def main(argv=None):
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--blogofile", action="store_true", help="Run blogofile")
    parser.add_argument(
        "--zeekofile", action="store_true", help="Run zeekofile")
    parser.add_argument(
        "--type", choices=["git", "hg"],
        help="Repository type", default="git")
    parser.add_argument(
        "--local-base", type=str,
        help="Full path to local directory for sites")
    parser.add_argument(
        "--local-prefix", type=str,
        help="Path prefix inside of a local site location")
    parser.add_argument(
        "--repo-prefix", type=str,
        help="Optional path prefix inside the repo itself")
    parser.add_argument(
        "--dry", action="store_true", help="Don't actually publish")
    parser.add_argument(
        "--domain", type=str,
        help="Fully qualified domain name, defaults to dirname of repo")
    parser.add_argument("source", type=str, help="Source repository path")
    parser.add_argument(
        "destination", choices=["local", "s3"], help="Destination")
    args = parser.parse_args(argv)

    repo = os.path.abspath(args.source)

    sitename = args.domain
    if not sitename:
        sitename = os.path.basename(repo)
        if sitename.endswith(".git"):
            sitename = sitename[0:-4]
    log("Site name %s", sitename)

    work_dir = os.path.join(os.path.dirname(repo), "work")
    if not os.path.exists(work_dir):
        log("creating work directory %s", work_dir)
        os.mkdir(work_dir)

    if args.type == 'hg':
        checkout = hg_checkout_files(repo, work_dir, sitename)
    elif args.type == 'git':
        checkout = git_checkout_files(repo, work_dir, sitename)

    if args.repo_prefix:
        checkout = os.path.join(checkout, args.repo_prefix)

    if args.blogofile:
        copy_from = blogofile_build(checkout)
    elif args.zeekofile:
        copy_from = zeekofile_build(checkout)
    else:
        copy_from = checkout

    if args.destination == "local":
        publish_local(
            copy_from, sitename, args.local_base,
            args.local_prefix, args.dry)
    elif args.destination == "s3":
        publish_s3(copy_from, sitename, args.dry)

if __name__ == '__main__':
    main()
