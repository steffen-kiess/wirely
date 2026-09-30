#!/usr/bin/python3
#
# Copyright (c) 2010-2012 Steffen Kieß
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile


def runget(command, *, cwd, env=None, input=None):
    result = subprocess.run(command, check=True, cwd=cwd, env=env,
                            input=input, stdout=subprocess.PIPE)
    return result.stdout.decode('utf-8').rstrip('\n')


def write_wd_tree(file_overrides=None):
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    index_path = (repo_root / runget(['git', 'rev-parse', '--git-path', 'index'],
                                     cwd=repo_root)).resolve()
    with tempfile.NamedTemporaryFile(prefix='tmp-index-',
                                     dir=str(index_path.parent)) as temporary_index:
        shutil.copyfile(str(index_path), temporary_index.name)
        env = dict(os.environ, GIT_INDEX_FILE=temporary_index.name)
        subprocess.run(['git', 'add', '--all'], check=True, cwd=repo_root,
                       env=env)
        if file_overrides:
            for path, content in file_overrides.items():
                entries = runget(['git', 'ls-files', '--stage', '--', path],
                                 cwd=repo_root, env=env).splitlines()
                if len(entries) != 1 or not entries[0].endswith('\t' + path):
                    raise ValueError('Expected one tracked index entry for ' + path)
                mode, _, stage = entries[0].split('\t', 1)[0].split()
                if stage != '0':
                    raise ValueError('Unmerged index entry for ' + path)
                blob = runget(['git', 'hash-object', '-w', '--stdin'],
                              cwd=repo_root, env=env, input=content)
                subprocess.run(['git', 'update-index', '--cacheinfo',
                                mode, blob, path], check=True, cwd=repo_root,
                               env=env)
        return runget(['git', 'write-tree'], cwd=repo_root, env=env)


def main(argv):
    parser = argparse.ArgumentParser(description='Write a working-tree Git tree')
    args = parser.parse_args(argv)

    try:
        tree = write_wd_tree()
    except subprocess.CalledProcessError as error:
        return error.returncode

    print(tree)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
