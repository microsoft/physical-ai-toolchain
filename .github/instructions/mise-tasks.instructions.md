---
description: when working with mise for tasks in the mise.toml file, follow these instructions to ensure proper usage and organization. Mise should be used for all additional tool installs
---

<!-- applyTo: '**/*.toml' -->

# Use Mise.jdx.dev for all additional tool installs

tools such as gh, uv, az, cmake, rust, etc.  -- ask if not sure

## use UV for all python

Always use uv for all python installs and never use system python. Use uv to install python and then use uv to install pip and then use uv to install any additional python packages.
use uv venv and mise to create a virtual environment for your project. This will ensure that your project dependencies are isolated and do not interfere with system packages.

Never pollute system python

## Use Mise toml correctly

### Task Grouping

Tasks can be grouped semantically by using name prefixes separated with :s. For example all testing related tasks may begin with test:. Nested grouping can also be used to further refine groups and simplify pattern matching. For example running mise run test:**:local will match test:units:local, test:integration:local and test:e2e:happy:local (See Wildcards for more information).

TIP

Since TOML keys can't contain colons without quoting, use quoted keys in mise.toml:

[tasks."test:unit"]
run = 'cargo test --lib'

### Run Tasks

Do not put embedded scripts in the TOML -- instead

- for platform overall -- put that in 'gpu-offload/scripts'
- for example specific -- put in 'gpu-offload/examples' with some prefix that correlates to the example name

### Task ordering

The goal of task ordering is to allow a human to "git clone..." then go through the setup, verification, coding, etc., then teardown --
Utilize "Task Grouping" in mise.toml to assist in that order so when "mise tasks" is execute the human can see the logical order of how to run things -- that means align the alphanumeric sort to accommodate. -- Always prefix the task name with a Alpha -- not a number or symbol -
