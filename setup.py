from setuptools import setup

setup(name='publishthing',
      version=1.0,
      description="mike's homegrown static publishing thing",
      classifiers=[
          'Development Status :: 4 - Beta',
          'Environment :: Console',
          'Programming Language :: Python',
          'Programming Language :: Python :: Implementation :: CPython',
      ],
      author='Mike Bayer',
      author_email='mike@zzzcomputing.com',
      url='http://bitbucket.org/zzzeek/publishthing',
      license='MIT',
      packages=["publishthing", "publishthing.apps"],
      zip_safe=False,
      install_requires=['webob', "boto", "requests", "unidiff"],
      entry_points={
          'console_scripts': [
              'publishthing = publishthing.apps.generate_site:main',
              'sync_github_issues = publishthing.apps.sync_gh_issues:main',
          ],
      })
