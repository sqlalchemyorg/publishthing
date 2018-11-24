============
publishthing
============

zzzeek's scripty thing for pushing content to the website.

Includes:

* bitbucket and github push hooks that pull down the latest repo and mirror
  out to other remotes

* A utility to pull down Github issues from the API and create a directory
  tree of all the json and the attachments, similarly to how a bitbucket
  issue export works

* blogofile and zeekofile build frontends that are usually used as git hooks,
  so that when you push to a certain repo, blogofile / zeekofile runs and
  rebuilds a static site.

* the builders also can push to amazon S3 but we aren't using that
  right now for sqlalchemy/mako etc. (however zzzeek's dad's sites use it)
