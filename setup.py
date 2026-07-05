from setuptools import setup, find_packages

setup(
    name="sift",
    version="0.1.0",
    packages=find_packages(),
    include_package_data=True,
    license="Non-Commercial Use License (See LICENSE file)",
    install_requires=[
        "duckduckgo_search>=6.2.0",
        "trafilatura>=2.0.0",
        "requests>=2.30.0",
        "httpx>=0.28.0",
        "click>=8.0.0",
    ],
    entry_points={
        "console_scripts": [
            "sift=sift.cli:main",
        ],
    },
)
