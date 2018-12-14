import os

from . import publishthing  # noqa

from .git import GitRepo
from . import s3push

class Publisher:
    def __init__(self, thing: "publishthing.PublishThing") -> None:
        self.thing = thing

    def blogofile_build(
            self, git_checkout: GitRepo, subdir: str = None) -> str:

        return self._ofile_build("blogofile", git_checkout, subdir=subdir)

    def zeekofile_build(
            self, git_checkout: GitRepo, subdir: str = None) -> str:
        return self._ofile_build("zeekofile", git_checkout, subdir=subdir)

    def _ofile_build(
            self, cmd: str, git_checkout: GitRepo, subdir: str = None) -> str:
        self.thing.message("building with %s", cmd)

        checkout = git_checkout.checkout_location
        if subdir:
            checkout = os.path.join(checkout, subdir)
        self.thing.message("base dir %s", checkout)
        with self.thing.shell_in(checkout) as shell:
            shell.call_shell_cmd(cmd, "build")
        return os.path.join(checkout, "_site")

    def publish_local(
            self, copy_from: str, sitename: str,
            local_base: str, local_prefix: str, dry: bool) -> None:
        site_location = os.path.join(local_base, sitename)
        if not os.path.exists(site_location):
            raise Exception(
                "Site location '%s' does not exist" % site_location)

        if local_prefix:
            dest = os.path.join(site_location, local_prefix)
            if not os.path.exists(dest):
                raise Exception(
                    "Site location '%s' exists but has no "
                    "subdirectory '%s'" % (
                        site_location, local_prefix
                    )
                )
        else:
            dest = site_location

        with self.thing.shell_in(dest) as shell:
            self.thing.message(
                "%sCopying %s to %s",
                "(dry) " if dry else "",
                copy_from,
                dest)
            if not dry:
                shell.call_shell_cmd(
                    "bash", "-c", "cp -R %s/* %s" % (copy_from, dest))

    def publish_s3(self, copy_from: str, sitename: str, dry: bool) -> None:
        self.thing.message(
            "%sPublishing %s to S3 bucket %s",
            "(dry) " if dry else "", copy_from, sitename)
        if not dry:
            s3push.s3_upload(self.thing, sitename, copy_from)


