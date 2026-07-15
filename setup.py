from setuptools import setup, find_packages

setup(
    name="sift",
    version="0.1.0",
    packages=find_packages(),
    include_package_data=True,
    license="Personal Use Only (See LICENSE file)",
    python_requires=">=3.10",
    install_requires=[
        "ddgs>=9.0.0",
        "python-dotenv>=1.0.0",
        "trafilatura>=2.0.0",
        "requests>=2.30.0",
        "httpx>=0.28.0",
        "click>=8.0.0",
    ],
    extras_require={
        "encrypted": ["sqlcipher3>=0.6.2"],
    },
    entry_points={
        "console_scripts": [
            "sift=sift.cli:main",
        ],
    },
)
