============
publishthing
============

zzzeek's scripty thing for pushing content to the website.

Includes:

* bitbucket hooks for receiving bitbucket push events and forwarding the
  git push off to github

* a blogofile build frontend that is usually used as a git hook, so that
  when you push to a certain repo, blogofile runs and rebuilds a static
  site.

* the blogofile builder also can push to amazon S3 but we aren't using that
  right now for sqlalchemy/mako etc. (however zzzeek's dad's sites use it)
