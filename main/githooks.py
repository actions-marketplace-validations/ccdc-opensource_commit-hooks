#!/usr/bin/env python3
'''
Module for a git hook.

'''

from collections import defaultdict
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import patch
import os
import platform
import re
import subprocess
import unittest
import sys


# Absolute file size limit (in MB) - it's 100MB on github.com
HARD_SIZE_THRESHOLD = 99.0
# Internal file size limit (in MB) - allow if commit message includes marker
SOFT_SIZE_THRESHOLD = 5.0
# Large file marker in commit message
LARGE_FILE_MARKER = 'LARGE_FILE'
# No jira marker in commit message
NO_JIRA_MARKER = 'NO_JIRA'
# A marker to represent it's a change we don't want to commit
DO_NOT_COMMIT = 'do not' + ' commit'
# Check file content if it has these extensions
CHECKED_EXTS = [
        '.bat',
        '.c',
        '.cgi',
        '.cmake',
        '.cpp',
        '.cs',
        '.css',
        '.F',
        '.f',
        '.h',
        '.inc',
        '.inl',
        '.java',
        '.js',
        '.php',
        '.pri',
        '.pro',
        '.ps1',
        '.py',
        '.sed',
        '.sh',
        '.svc',
        '.tpl',
        ]
# File types that need a terminating newline
TERMINATING_NEWLINE_EXTS = ['.c', '.cpp', '.h', '.inl']

def _get_output(command_list, cwd='.'):
    return subprocess.check_output(command_list, cwd=cwd).decode(errors='replace')


def _is_github_event():
    if 'GITHUB_EVENT_NAME' in os.environ:
        return True
    return False


def _is_pull_request():
    if os.environ.get('GITHUB_EVENT_NAME', '') == 'pull_request':
        return True
    else:
        return False


def _skip(filename, msg):
    print(f'SKIP {filename}: {msg}')


def _fail(msg):
    print(f'COMMIT FAIL: {msg}')


def _is_windows():
    return platform.system() == 'Windows'


def get_user():
    '''Get user making the commit'''
    if _is_github_event():
        return os.environ['GITHUB_ACTOR']
    else:
        output = _get_output(['git', 'var', 'GIT_AUTHOR_IDENT'])
        match = re.match(r'^(.+) <', output)
        return match.group(1)


def get_branch():
    '''Get current branch'''
    if _is_github_event():
        if _is_pull_request():
            return os.environ['GITHUB_HEAD_REF']
        else:
            return os.environ['GITHUB_REF'].split('/')[-1]
    else:
        return _get_output(['git', 'branch']).split()[-1]


def get_file_content_as_binary(filename):
    '''Get content of a file in binary mode

    Locally (ie. non-github event) we return the content of the staged file,
    not the file in the working directory.
    '''
    if _is_github_event() or 'pytest' in sys.modules:
        try:
            with open(filename, 'rb') as fileobj:
                data = fileobj.read().decode()
        except UnicodeDecodeError:
            _skip(filename, 'File is not UTF-8 encoded')
            data = None
    else:
        data = _get_output(['git','show', f':{filename}'])
    return data


def get_text_file_content(filename):
    '''Get content of a text file

    Locally (ie. non-github event) we return the content of the staged file,
    not the file in the working directory.
    '''
    if _is_github_event() or 'pytest' in sys.modules:
        data = Path(filename).read_text()
    else:
        data = _get_output(['git', 'show', f':{filename}'])
    return data


def get_sha():
    '''Get the commit sha

    The sha of the branch we are interested in, ie. the tip of the branch that
    is pushed or to be merged to main.

    GITHUB_SHA cannot be used because in a pull request it gives the sha of the
    fake merge commit.
    '''
    return _get_output(['git','rev-parse', get_branch()])


def get_event():
    '''Get the git event'''
    if _is_github_event():
        return os.environ['GITHUB_EVENT_NAME']
    else:
        return 'commit'


def get_branch_files():
    '''Get all files in branch'''
    return _get_output(['git','ls-tree', '-r', get_branch(),'--name-only']).splitlines()


def add_file_to_index(filename):
    '''Add file to current commit'''
    return _get_output(['git','add',filename])


def get_commit_files():
    '''Get files in current commit

    Return a dictionary:
        'M': <list of modified files>
        'A': <list of new files>

    '''
    if _is_github_event():
        commands = ['git','diff','--ignore-submodules','--name-status']
        if _is_pull_request():
            commands += [f'remotes/origin/{os.environ["GITHUB_BASE_REF"]}..remotes/origin/{os.environ["GITHUB_HEAD_REF"]}','--']
        else:
            commands += ['HEAD~..', '--']
    else:
        commands = ['git', 'diff-index', '--ignore-submodules', 'HEAD', '--cached']
        
    output = _get_output(commands)
    result = defaultdict(list)
    for line in output.splitlines():
        parts = line.split()
        if parts[-2] in ['M', 'A']:
            result[parts[-2]].append(parts[-1])
    return result


def parse_diff_header(header_line):
    '''Parse "git diff --unified=0" header lines to get changed line numbers

    The line number is in relation to the file after the change.

    :param header_line: A header in the git diff --unified=0 output
    :returns: a string that represents either a line number or a range
    '''
    match = parse_diff_header.pattern.match(header_line)
    start = int(match.group(1))
    if match.group(2):
        num = int(match.group(3))
        if num > 0:
            changed_lines = f'{start}-{start+num-1}'
        else:
            changed_lines = str(start)
    else:
        changed_lines = str(start)
    return changed_lines
parse_diff_header.pattern = re.compile(r'^@@\s[^\s]+\s\+?(\d+)(,(\d+))?\s@@.*')


class TestParseDiffHeaderPattern(unittest.TestCase):
    def test_various_strings(self):
        def _test(input, output):
            self.assertEqual(output, parse_diff_header(input))
        _test('@@ -142 +178 @@', '178')
        _test('@@ -142 +178,3 @@', '178-180')
        _test('@@ -142 +178,7 @@', '178-184')
        _test('@@ -3,0 +3 @@', '3')
        _test('@@ -1 +0,0 @@', '0')


def get_changed_lines(modified_file):
    '''New and modified lines in modified file in current commit

    Depending on the context, the change is defined as (old -> new):

        pull request:   BASE -> HEAD    (ie. main -> feature_branch)
        push:           HEAD~ -> HEAD   (ie. previous commit -> current commit)
        precommit:      HEAD -> index   (ie. current commit -> new commit)

    The returned list contains line numbers of lines that have changed. The
    list can contain ranges. For example ['3','6-9','12'] means lines
    3,6,7,8,9,12.

    :param modified_file: The file which has changed
    :returns: A list of line number (integers and ranges) of changed lines
    '''
    if _is_github_event():
        commands = ['git','diff','--unified=0']
        if _is_pull_request():
            commands += [f'remotes/origin/{os.environ["GITHUB_BASE_REF"]}..remotes/origin/{os.environ["GITHUB_HEAD_REF"]}', '--',f'{modified_file}']
        else:
            commands += ['HEAD~', f'{modified_file}']
    else:
        commands = [f'git', 'diff-index', 'HEAD', '--unified=0', f'{modified_file}']
    output = _get_output(commands)

    lines = []
    for line in output.splitlines():
        if not line.startswith('@@'):
            continue
        lines.append(parse_diff_header(line))
    return lines


def yield_changed_lines(changed_lines):
    '''Yield individual line numbers from list returned by get_changed_lines'''
    for line_num_range in changed_lines:
        if '-' in line_num_range:
            start, end = map(int, line_num_range.split('-'))
        else:
            start, end = int(line_num_range), int(line_num_range)
        for line_num in range(start, end+1):
            yield line_num


class TestYieldChangedLines(unittest.TestCase):
    def test_various_lists(self):
        def _test(input, output):
            self.assertListEqual(output, list(yield_changed_lines(input)))
        _test(['12'], [12])
        _test(['12-15'], [12,13,14,15])
        _test(['12-15','44','55-57'], [12,13,14,15,44,55,56,57])


def get_config_setting(setting):
    '''Get the value of a config setting'''
    try:
        return _get_output(['git', 'config', '--get', setting]).strip()
    except subprocess.CalledProcessError:
        return None


def check_eol(files):
    '''Check line endings if autocrlf is not configured correctly.

    If autocrlf is configured as recommended, we skip this check.

    Recommended configuration:
        1. On Windows: `git config --global core.autocrlf true`
        2. Otherwise (including WSL): `git config --global core.autocrlf input`

    Otherwise check all the text files for LF line endings.
    '''
    autocrlf = get_config_setting('core.autocrlf')
    if _is_windows():
        if autocrlf == 'true':
            return 0
    else:
        if autocrlf == 'input':
            return 0

    # As the client environment is not configured with autocrlf
    # we need to ensure that every text file does not contain CRLF.
    for filename in files:
        data = get_file_content_as_binary(filename)
        if data is None:
            continue

        # Skip binary file
        if '\0' in data:
            continue

        # Exception for .bat as they are only run in windows environments.
        if data.find('\r\n') != -1 and not filename.endswith(".bat"):
            _fail(f'Bad line endings in {filename}')
            return 1
    return 0


def check_do_not_merge_in_file(filename, new_file=False):
    '''Check for "do not merge" in a filename'''
    data = get_file_content_as_binary(filename)
    if data is None:
        return 0
    else:
        lines = data.splitlines(True)

    if new_file:
        line_nums = [f'1-{len(lines)}']
    else:
        line_nums = get_changed_lines(filename)

    for line_num in yield_changed_lines(line_nums):
        try:
            line = lines[line_num-1]
        except IndexError as exc:
            print(f'Error {exc}: {line_num-1} in {filename}')
            continue
        if 'do not merge' in line.lower():
            _fail(f'Found DO NOT MERGE in "{filename}".')
            return 1

    return 0


def check_do_not_merge(files, new_files=False):
    '''Check for "do not merge" in files

    This check is case insensitive.

    Note that if found this will abort the merge, leaving it in a merge
    conflict resolution state. User should either simply
        1. Run "git merge --abort", or
        2. Fix the issue (eg. by removing the offending file or part from the
           index) before doing "git commit" to complete the merge.

    '''
    retval = 0
    for filename in files:
        retval += check_do_not_merge_in_file(filename, new_files)
    return retval


def trim_trailing_whitespace(string):
    '''Return a string with trailing white spaces removed'''
    return trim_trailing_whitespace.pattern.sub(r"\1", string)
trim_trailing_whitespace.pattern = re.compile(r"\s*?(\r?\n|$)")


class TestTrailingWhitespacePattern(unittest.TestCase):
    def test_various_strings(self):
        def _test(input, output=None):
            if output is None:
                output = input
            self.assertEqual(output, trim_trailing_whitespace(input))
        _test('')
        _test('\n')
        _test('\r\n')
        _test('a')
        _test(' a')
        _test('  a')
        _test('1234')
        _test(u'abcd\xe9')
        _test(' ', '')
        _test(' \n', '\n')
        _test(' \r\n', '\r\n')
        _test(' a ', ' a')
        _test('  a ', '  a')
        _test(' a \r\n  b   \n c\n ', ' a\r\n  b\n c\n')
        _test(u'abcd\xe9 ', u'abcd\xe9')


def trim_trailing_whitespace_in_file(filename, new_file, dry_run,
                                     add_to_git_index=True):
    '''Remove trailing white spaces in new and modified lines in a filename

    :param filename: The file to check
    :param new_file: True if whole file is new; False if it's an existing
        file that's been modified
    :param dry_run: True if we don't want to actually update the file - just
        return a value to indicate if trailing whitespace is found or not;
        False if the file is to be updated if trailing whitespace is found
    :param add_to_git_index: If dry_run=False, set to False if we don't want to
        automatically add the new file to git index should it be updated
    :returns: If dry_run=True, 0 if no trailing whitespace is found, 1 if
        trailing whitepsace is found.
    '''
    data = get_file_content_as_binary(filename)
    if data is None:
        return 0
    else:
        lines = data.splitlines(True)

    if new_file:
        line_nums = [f'1-{len(lines)}']
    else:
        line_nums = get_changed_lines(filename)

    modified_file = False   # if trimming white space in place
    modified_lines = []     # if flagging instead of trimming in place

    for line_num in yield_changed_lines(line_nums):
        try:
            before = lines[line_num-1]
        except IndexError as exc:
            print(f'Error {exc}: {line_num} in {filename}')
            continue
        after = trim_trailing_whitespace(before)
        if before != after:
            if dry_run:
                modified_lines.append(str(line_num))
            else:
                print(f'   Fixed line {filename}:{line_num}')
                modified_file = True
                lines[line_num-1] = after

    if modified_file:
        with open(filename, 'wb') as fileobj:
            lines = ''.join(lines)
            fileobj.write(lines.encode())
        if add_to_git_index:
            add_file_to_index(filename)

    if modified_lines:
        _fail(f'Found trailing white space in {filename} at lines: ' +
              ','.join(modified_lines))
        return 1

    return 0


class TestTrimTrailingWhitespace(unittest.TestCase):
    def test_trim_trailing_whitespace(self):
        content = 'first line\nsecond line \nthird line '
        trimmed_content = 'first line\nsecond line\nthird line'

        name = NamedTemporaryFile().name        
        try:
            Path(name).write_text(content)
            # Trailing whitespace found
            retval = trim_trailing_whitespace_in_file(name, True, True)
            self.assertEqual(retval, 1)
            self.assertEqual(Path(name).read_text(), content)

            # Now remove the trailing whitespace
            trim_trailing_whitespace_in_file(name, True, False, False)
            # Trailing whitespace no longer found
            self.assertEqual(Path(name).read_text(), trimmed_content)
            retval = trim_trailing_whitespace_in_file(name, True, True)
            self.assertEqual(retval, 0)
        finally:
            Path(name).unlink()


    def test_decodeerror(self):
        # A text file that is not utf-8 encoded - report and skip
        test_file = Path(__file__).parent / '../test/decode_error.txt'
        with patch('sys.stdout', new=StringIO()) as tmp_stdout:
            retval = trim_trailing_whitespace_in_file(test_file, True, True)
            self.assertEqual(retval, 0)
            self.assertEqual(tmp_stdout.getvalue().strip(), f'SKIP {test_file}: File is not UTF-8 encoded')


def remove_trailing_white_space(files, new_files=False, dry_run=False):
    '''Remove trailing white spaces in all new and modified lines

    Set dry_run to True if you just want to check if trailing whitespace exists
    in the file instead of actually updating the file.
    '''
    retval = 0
    for filename in files:
        retval += trim_trailing_whitespace_in_file(filename, new_files,
                                                   dry_run)
    return retval


def check_filename(filepath):
    # We permit repository paths to be up to 50 characters long excluding the
    # final slash character.
    # Windows allows paths with up to 259 characters (260 including a
    # terminating null char)
    max_subpath_chars = 208

    # It's easy to add files on Linux that will make the repository unusable
    # on Windows.
    # Windows filename rules are here:
    # http://msdn.microsoft.com/en-us/library/windows/desktop/aa365247.aspx#naming_conventions
    # This checks for those cases and stops the commit if found.

    # Filename must not contain these characters
    ILLEGAL_CHARS = frozenset('\\/:*?"<>|')
    # These names are reserved on Windows
    DEVICE_NAMES = frozenset([
        'con', 'prn', 'aux', 'nul',
        'com1', 'com2', 'com3', 'com4', 'com5', 'com6', 'com7', 'com8', 'com9',
        'lpt1', 'lpt2', 'lpt3', 'lpt4', 'lpt5', 'lpt6', 'lpt7', 'lpt8', 'lpt9'
        ])

    filename = Path(filepath).name
    for ch in filename:
        if ch in ILLEGAL_CHARS or ord(ch) <= 31:
            _fail(f'Illegal character "{ch}" in filename "{filename}".')
            return 1

    if Path(filename).stem in DEVICE_NAMES:
        _fail(f'Illegal filename "{filename}" - reserved on Windows.\n')
        return 1

    if filepath[-1] == '.' or filepath[-1].isspace():
        _fail(f'Illegal file name "{filepath}" - '
              'names are not permitted to end with "." or whitespace.')
        return 1

    try:
        filepath.encode('ascii')
    except UnicodeEncodeError:
        _fail(f'Illegal path "{filepath}" - '
              'only ASCII characters are permitted.')
        return 1

    if len(filepath) > max_subpath_chars:
        _fail(f'File path "{filepath}" is too long, it must be '
              f'{max_subpath_chars} characters or less.')
        return 1

    return 0


class TestCheckFileName(unittest.TestCase):
    def test_various_strings(self):
        def _test(input, output):
            self.assertEqual(output, check_filename(input))
        _test('good/some.txt', 0)
        _test('bad/illegal/star*star.txt', 1)
        _test('bad/reserved/device/con.txt', 1)
        _test('bad/end/period.txt.', 1)
        _test('bad/end/space.txt ', 1)
        _test('bad/ascii/你好.txt', 1)
        _test('long/path/'*20 + 'l208.txt', 0)
        _test('long/path/'*20 + 'll209.txt', 1)


def check_filenames(files):
    '''Check file path and name meet requirement.

    For file path, specifically that it's all ASCII and roughly within max
    length on Windows.

    For file name, check for case conflict, that it does not include illegal
    characters or Windows reserved namess, and does not end in a period or
    whitespace.

    '''

    # This issue is only possible on Linux
    if not _is_windows():
        manifest_lower2case = {f.lower(): f for f in get_branch_files()}
        commit_files = get_commit_files()
        for commit_type in commit_files:
            for f in commit_files[commit_type]:
                flower = f.lower()
                if (flower in manifest_lower2case and
                        manifest_lower2case[flower] != f):
                    _fail(f'Case-folding collision between "{f}" and '
                          f'"{manifest_lower2case[flower]}"')
                    return 1
                else:
                    manifest_lower2case[flower] = f

    retval = 0
    for filepath in files:
        retval += check_filename(filepath)
    return retval


def check_username():
    '''Check username of person making the commit

    In git, this is the *author* of the commit.

    Check for reasonable username (ie. made up of alphabets), and that it's
    not a build service account or root account.

    '''

    username = get_user()
    if re.search(r'root|buildman|[^a-zA-Z ]', username) is not None:
        message = 'Bad username "' + username + '"\n'
        if username == 'buildman' or username == 'root':
            message += 'buildman or root user should not be used'
        else:
            message += 'To set this up see https://docs.github.com/en/github/using-git/setting-your-username-in-git'
        _fail(message)
        return 1

    return 0


def check_file_content(filename, data):
    if DO_NOT_COMMIT in data.lower():
        _fail(f'Found {DO_NOT_COMMIT.upper()} in "{filename}".')
        return 1

    if '\t' in data:
        _fail(f'Found tab characters in "{filename}".')
        return 1

    # For file types that need a terminating newline
    if any(map(lambda ext: filename.endswith(ext), TERMINATING_NEWLINE_EXTS)):
        if not data.endswith('\n'):
            _fail(f'Missing terminating newline in {filename}.')
            return 1

    # NOTE: Not checking eol

    # Detect common C++ errors that the build-checkers have encountered.
    if any(map(lambda ext: filename.endswith(ext), ['.cpp', '.h', '.inl'])):
        num = 0
        for line in data.splitlines():
            num += 1
            if cpp_include_backslash_pattern.search(line):
                _fail(f'{filename}:{num} - Backslash in #include.')
                return 1
            if cpp_throw_std_exception_pattern.search(line):
                _fail(f'{filename}:{num} - std::exception thrown.')
                return 1

    return 0
cpp_include_backslash_pattern = re.compile('^\\s*\\#\\s*include\\s*[\\"\\<][^\\"\\>]*\\\\', re.MULTILINE)
cpp_throw_std_exception_pattern = re.compile(r'\bthrow\s+(std\s*::\s*)?exception\s*\(')


class TestCppIncludeBackslashPattern(unittest.TestCase):
    def test_no_path_separator(self):
        self.assertIsNone(
                cpp_include_backslash_pattern.search('#include <iostream>'))
        self.assertIsNone(
                cpp_include_backslash_pattern.search('#include "header.h"'))

    def test_commented_out(self):
        self.assertIsNone(
                cpp_include_backslash_pattern.search('//#include "a\\b"'))

    def test_multiline(self):
        self.assertIsNotNone(
                cpp_include_backslash_pattern.search(
                    'a\nb\n#include "a\\b"\nc\nd\n'))

    def do_test_with_each_separator(self, *args):
        good = "/".join(args)
        bad = "\\".join(args)
        self.assertIsNone(cpp_include_backslash_pattern.search(good))
        self.assertIsNotNone(cpp_include_backslash_pattern.search(bad))

    def test_angle_brackets(self):
        self.do_test_with_each_separator('#include <some', 'path>')

    def test_quotes(self):
        self.do_test_with_each_separator('#include "another', 'file"')

    def test_unusual_characters(self):
        self.do_test_with_each_separator(
                '#include "1', "! 2£$€%^&()", "-_=+[{]};'@#~,.", '`¬¦"')

    def test_space(self):
        self.do_test_with_each_separator(' #include "a', 'b"')
        self.do_test_with_each_separator('  #include "c', 'd"')
        self.do_test_with_each_separator('     #include "e', 'f"')
        self.do_test_with_each_separator('\t#include "e', 'f"')
        self.do_test_with_each_separator('# include "g', 'h"')
        self.do_test_with_each_separator('#  include "i', 'j"')
        self.do_test_with_each_separator('#include"i', 'j"')
        self.do_test_with_each_separator('#include<k', 'l>')
        self.do_test_with_each_separator(
                '     #      include           "       m      ',
                '  n        "     ')

    def test_comment(self):
        self.do_test_with_each_separator(
                '#include "x', 'y"// back\\slashes\\ in comment')


class TestCppThrowStdExceptionPattern(unittest.TestCase):
    def test_find(self):
        self.assertIsNotNone(
                cpp_throw_std_exception_pattern.search(
                    'throw std::exception();'))
        self.assertIsNotNone(
                cpp_throw_std_exception_pattern.search(
                    'throw exception("string")'))
        self.assertIsNotNone(
                cpp_throw_std_exception_pattern.search('throw exception()'))
        self.assertIsNotNone(
                cpp_throw_std_exception_pattern.search(
                    ' {throw exception();}//comment'))

    def test_spaces(self):
        self.assertIsNotNone(
                cpp_throw_std_exception_pattern.search(
                    ' throw  std   ::    exception     (   )  '))

    def test_dont_find_runtime_error(self):
        self.assertIsNone(
                cpp_throw_std_exception_pattern.search(
                    'throw std::runtime_error();'))

    def test_dont_find_variable_named_exception(self):
        self.assertIsNone(
                cpp_throw_std_exception_pattern.search('throw exception'))
        self.assertIsNone(
                cpp_throw_std_exception_pattern.search('throw exception;'))

    def test_dont_find_catch_exception(self):
        self.assertIsNone(
                cpp_throw_std_exception_pattern.search(
                    'catch (std::exception)'))

    def test_match_word_boundaries(self):
        self.assertIsNone(
                cpp_throw_std_exception_pattern.search('throw exceptionblah'))
        self.assertIsNone(
                cpp_throw_std_exception_pattern.search('rethrow exception'))


class TestCheckFileContent(unittest.TestCase):
    def test_various_files(self):
        def _test(filename, is_good, data=None):
            test_file = Path(__file__).parent / f'../test/{filename}'
            if data is None:
                data = get_file_content(str(test_file))
            retval = check_file_content(filename, data)
            self.assertEqual(retval == 0, is_good)
        def _test_good_file(filename, data=None):
            _test(filename, True, data=data)
        def _test_bad_file(filename, data=None):
            _test(filename, False, data=data)
        _test_bad_file('do_not_commit.py', data='do not ' + 'commit')
        _test_bad_file('tab.py', data='field\tfield')
        _test_bad_file('no_newline.cpp', data='No terminating newline')
        _test_good_file('good_file.cpp')


def get_file_content(filename):
    '''Return the content of a file.

    We do so if:
        1. Filename has certain extensions
        2. The content can be read
        3. It's a text file

    Otherwise return None
    '''
    # Skip file if extension is not in the checked list
    if not any([filename.endswith(checked_ext)
                for checked_ext in CHECKED_EXTS]):
        return

    # NOTE: ignored_patterns not implemented

    try:
        data = get_text_file_content(filename)
    except Exception as exc:
        print(f'Error "{exc}" while reading {filename}')
        return

    # Skip binary file
    if '\0' in data:
        _skip(filename, 'Not a text file')
        return

    return data


def check_content(files):
    '''Check content of files.

    This only applies to files meeting the conditions:
        1. Filename has certain extensions
        2. The content can be read
        3. It's a text file

    We check that:
        1. It does not contain DO_NOT_COMMIT (case insensitive)
        2. It does not contain tab
        3. For C / C++ source files:
            a. It has no missing newline at the end
            b. It has no backslash in #include
            c. It does not throw std::exception

    '''
    retval = 0

    for filename in files:
        data = get_file_content(filename)
        if data is not None:
            retval += check_file_content(filename, data)

    return retval


def check_commit_msg(message, files):
    '''Check commit message (and file size).

    Abort if file size exceeds hard (github.com) limit.

    If file size exceeds our soft (internal) limit, flag up if commit message
    does not contain required marker.

    '''
    if re.match(r'^Merge ((remote-tracking )?branch|commit) \'.+?\'( of [^\s]+)? into .+', message):
        # Not checking for JIRA or large file in commit message generated by github
        return 0

    if re.match(r'^Merge pull request #.+ from .+', message):
        # Not checking for JIRA or large file in commit message generated by github
        return 0

    if NO_JIRA_MARKER not in message:
        if jira_id_pattern.search(message) is None:
            _fail('Every commit should contain a Jira issue ID or the text '
                  f'{NO_JIRA_MARKER}')
            return 1

    for filename in files:
        size = Path(filename).stat().st_size / 1024**2
        if size > HARD_SIZE_THRESHOLD:
            _fail(f'{filename} is larger than the github limit.')
            return 1
        elif size > SOFT_SIZE_THRESHOLD:
            if LARGE_FILE_MARKER not in message:
                _fail(f'{filename} is larger than {SOFT_SIZE_THRESHOLD}MB.')
                return 1

    return 0
jira_id_pattern = re.compile(r'\b[A-Z]{2,8}-[0-9]{1,5}\b')


class TestJiraIDPattern(unittest.TestCase):
    def test_various_strings(self):
        def _test(input, is_jira=True):
            m = jira_id_pattern.search(input)
            self.assertEqual(bool(m), is_jira)
        _test('BLD-5704')
        _test('CQ-1')
        _test('CQ-12345')
        _test('SKETCHER-1')
        _test('SKETCHER-12345')
        _test("BLD-1234 fixed some builds")
        _test("fixed some builds BLD-1234 ")
        _test("fixed some builds (Jira BLD-1234)")
        _test("fixed some builds\n some more text BLD-1234")
        _test('lower-1234', False)
        _test('A-1234', False)
        _test('ABCDEFGHI-1234', False)
        _test('BLD-123456', False)
        _test('wordBLD-1234', False)
        _test('BLD-1234word', False)


class TestCheckCommitMessage(unittest.TestCase):
    def test_various_strings(self):
        def _test(input, is_good=True):
            rc = check_commit_msg(input, [])
            self.assertEqual(rc == 0, is_good)
        _test('ABC-1234')
        _test('Some changes for ABC-1234 ticket')
        _test('Trivial change NO_JIRA')
        _test("Merge branch 'main' into my_branch")
        _test("Merge branch 'branch_1' into branch_2")
        _test("Merge branch 'jira_pyapi_123_abc' of github.com:ccdc-confidential/cpp-apps-main into jira_pyapi_123_abc")
        _test("Merge commit 'abcdef' into jira_mer_123_abc")
        _test("Merge remote-tracking branch 'origin/release/2022.1' into merge_from_release")
        _test("Merge pull request #1 from patch-1")
        _test('I forgot to add the jira marker!', False)
        _test('Close but no cigar abc-1234', False)


def commit_hook(merge=False):
    retval = 0
    files = get_commit_files()

    print(' Check username ...')
    retval += check_username()

    if merge:
        print(' Check do not merge ...')
        retval += check_do_not_merge(files['M'])
        retval += check_do_not_merge(files['A'], new_files=True)
    else:
        print(' Check filenames ...')
        retval += check_filenames(files['M'] + files['A'])

        print(' Check line endings ...')
        retval += check_eol(files['M'] + files['A'])

        print(' Check file content ...')
        retval += check_content(files['M'] + files['A'])

    return retval


def commit_msg_hook():
    retval = 0
    files = get_commit_files()
    commit_message = Path(sys.argv[1]).read_text()

    print(' Check commit message ...')
    retval += check_commit_msg(commit_message, files['M'] + files['A'])

    return retval
