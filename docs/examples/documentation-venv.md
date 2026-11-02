# Research report

**Objective:** Using the official Python documentation, find out how to create a virtual environment with venv, how to activate it on Windows and on macOS or Linux, and how to install packages into it.

**Status:** finished by the agent on turn 5

## Final result

To create a virtual environment using venv:
```python
python -m venv /path/to/new/virtual/environment
```
[1]

To activate the virtual environment on Windows:
```cmd
\venv\Scripts\activate
```
[1]

To activate the virtual environment on macOS or Linux:
```bash
source venv/bin/activate
```
[1]

To install packages into the virtual environment, use pip within the activated environment:
```python
python -m pip install package_name
```
[2]

## Checklist

The parts of the objective, with the discoveries recorded for each:

1. Find out how to create a virtual environment with venv from official Python documentation. (discoveries for it: [1])
2. Find out how to activate a virtual environment on Windows from official Python documentation. (discoveries for it: [1])
3. Find out how to activate a virtual environment on macOS or Linux from official Python documentation. (discoveries for it: [1])
4. Find out how to install packages into a virtual environment from official Python documentation. (discoveries for it: [2])

## Discoveries (2)

### 1. Turn 3, from https://docs.python.org/3/library/venv.html

To create a virtual environment, use the following command: python -m venv /path/to/new/virtual/environment. To activate the virtual environment on Windows, use the command: \venv\Scripts\activate. On macOS or Linux, use the command: source venv/bin/activate.

### 2. Turn 4, from https://docs.python.org/3/library/venv.html

To install packages into the virtual environment, use pip within the activated environment: python -m pip install package_name.
