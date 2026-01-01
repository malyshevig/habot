from setuptools import setup

setup(
    name='habot',
    version='0.1.4',
    packages=['habot'],
    package_dir={'': 'src'},
    url='',
    license='',
    author='Ilya Malyshev',
    author_email='',
    description=''
)

from setuptools import setup, find_packages

def readme():
  with open('README.md', 'r') as f:
    return f.read()

