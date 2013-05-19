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
      packages=["publishthing"],
      zip_safe=False,
      entry_points={
        'console_scripts': ['publishthing = publishthing.publishthing:main'],
      }
)
