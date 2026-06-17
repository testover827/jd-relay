from setuptools import setup, find_packages

setup(
    name="jdcli",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "click>=8.0",
        "httpx>=0.24",
        "pycryptodome>=3.19",
    ],
    entry_points={
        "console_scripts": [
            "jdcli=jdcli.main:main",
        ],
    },
)
