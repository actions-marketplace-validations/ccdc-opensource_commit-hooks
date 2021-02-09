#!/usr/bin/env python3
'''
This is a git hook migrated from hg.

Reference:
https://confluence.ccdc.cam.ac.uk/pages/viewpage.action?spaceKey=GIT&title=Hooks

'''

from collections import defaultdict
from pathlib import Path
import platform
import re
import subprocess


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


def _get_output(command, cwd='.'):
    return subprocess.check_output(command, shell=True, cwd=cwd).decode()


def get_user():
    '''Get user making the commit'''
    output = _get_output('git var GIT_AUTHOR_IDENT')
    match = re.match(r'^(.+) <', output)
    return match.group(1)


def get_branch():
    '''Get current branch'''
    return _get_output('git branch').split()[-1]


def get_branch_files():
    '''Get all files in branch'''
    branch = get_branch()
    return _get_output(f'git ls-tree -r {branch} --name-only').splitlines()


def add_file_to_index(filename):
    '''Add file to current commit'''
    return _get_output(f'git add {filename}')


def get_commit_files():
    '''Get files in current commit

    Return a dictionary:
        'M': <list of modified files>
        'A': <list of new files>

    '''
    output = _get_output('git diff-index HEAD')
    result = defaultdict(list)
    for line in output.splitlines():
        parts = line.split()
        if parts[-2] in ['M', 'A']:
            result[parts[-2]].append(parts[-1])
    return result


def get_changed_lines(modified_file):
    '''New and modified lines in modified file in current commit'''
    output = _get_output(f'git diff-index HEAD -p --unified=0 {modified_file}')
    lines = []
    for line in output.splitlines():
        if not line.startswith('@@'):
            continue
        match = get_changed_lines.pattern.match(line)
        start = int(match.group(1))
        if match.group(2):
            for num in range(int(match.group(3))):
                lines.append(start + num)
        else:
            lines.append(start)
    return lines
get_changed_lines.pattern = re.compile(r'^@@\s[^\s]+\s\+?(\d+)(,(\d+))?\s@@.*')


def check_do_not_merge_in_file(filename, new_file=False):
    '''Check for "do not merge" in a filename'''
    with open(filename, 'rb') as fileobj:
        lines = fileobj.read().decode().splitlines(True)

    if new_file:
        line_nums = range(1, len(lines)+1)
    else:
        line_nums = get_changed_lines(filename)

    for line_num in line_nums:
        try:
            line = lines[line_num-1]
        except IndexError as exc:
            print(f'Error {exc}: {line_num-1} in {filename}')
            continue
        if 'do not merge' in line.lower():
            print(f'   Found DO NOT MERGE in "{filename}".\n'
                  '   Run "git merge --abort" to start again, '
                  f'or remove {filename} from index before completing the '
                  'merge with "git commit".')
            return 1

    return 0


def check_do_not_merge(files, new_files=False):
    '''Check for "do not merge" in files

    This check is case insensitive.

    Note that if found this will abort the merge, leaving it in a merge
    conflict resolution state. User should either simply "git merge --abort"
    or fix the issue (eg. by removing the offending file or part from the
    index) before doing "git commit" to complete the merge.

    '''
    retval = 0
    for filename in files:
        print(f'  Checking file {filename}')
        retval += check_do_not_merge_in_file(filename, new_files)
    return retval


def trim_trailing_whitespace(string):
    '''Return a string with trailing white spaces removed'''
    return trim_trailing_whitespace.pattern.sub(r"\1", string)
trim_trailing_whitespace.pattern = re.compile(r"\s*?(\r?\n|$)")


def trim_trailing_whitespace_in_file(filename, new_file=False):
    '''Remove trailing white spaces in new and modified lines in a filename'''
    with open(filename, 'rb') as fileobj:
        lines = fileobj.read().decode().splitlines(True)

    if new_file:
        line_nums = range(1, len(lines)+1)
    else:
        line_nums = get_changed_lines(filename)

    modified_file = False

    for line_num in line_nums:
        try:
            before = lines[line_num-1]
        except IndexError as exc:
            print(f'Error {exc}: {line_num-1} in {filename}')
            continue
        after = trim_trailing_whitespace(before)
        if before != after:
            print(f'   Fixed line {line_num}')
            modified_file = True
            lines[line_num-1] = after

    if modified_file:
        with open(filename, 'wb') as fileobj:
            lines = ''.join(lines)
            fileobj.write(lines.encode())
        add_file_to_index(filename)


def remove_trailing_white_space(files, new_files=False):
    '''Remove trailing white spaces in all new and modified lines'''
    for filename in files:
        print(f'  Checking file {filename}')
        trim_trailing_whitespace_in_file(filename, new_files)


def check_filenames(files):
    '''Check file path and name meet requirement.

    For file path, specifically it's all ASCII and roughly within max length
    on Windows.

    For file name, check for case conflict, does not include illegal
    characters, reserved names on Windows, and does not end in a period or
    whitespace.

    '''

    # TODO: Test on a real linux / mac machine
    if platform.system() != 'Windows':
        manifest_lower2case = {}
        for f in get_branch_files():
            flower = f.lower()
            if flower in manifest_lower2case:
                print(f'   Case-folding collision between "{f}" and '
                      f'"{manifest_lower2case[flower]}"')
                return 1
            else:
                manifest_lower2case[flower] = f

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

    for filepath in files:
        print(f'  Checking file {filepath}')
        filename = Path(filepath).name
        for ch in filename:
            if ch in ILLEGAL_CHARS or ord(ch) <= 31:
                print(f'   Illegal character "{ch}" in filename "{filename}".')
                return 1

        if Path(filename).stem in DEVICE_NAMES:
            print(f'   Illegal filename "{filename}" - reserved on Windows.\n')
            return 1

        if filepath[-1] == '.' or filepath[-1].isspace():
            print(filepath)
            print(f'   Illegal file name "{filepath}" - '
                  'names are not permitted to end with "." or whitespace.')
            return 1

        try:
            filepath.encode('ascii')
        except UnicodeDecodeError:
            print(f'   Illegal path "{filepath}" - '
                  'only ASCII characters are permitted.')
            return 1

        if len(filepath) > max_subpath_chars:
            print(f'   File path "{filepath}" is too long, it must be '
                  f'{max_subpath_chars} characters or less.')
            return 1

    return 0


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
        print(message)
        return 1

    return 0


def check_file_content(filename, data):
    if 'do not commit' in data.lower():
        print(f'   Found DO NOT COMMIT in "{filename}". '
              'Remove file from index.')
        return 1

    if '\t' in data:
        print(f'   Found tab characters in "{filename}". Replace with spaces.')
        return 1

    # For file types that need a terminating newline
    if any(map(lambda ext: filename.endswith(ext),
               ['.c', '.cpp', '.h', '.inl'])):
        if not data.endswith('\n'):
            print(f'   Missing terminating newline in {filename}')
            return 1

    # NOTE: Not checking eol

    # Detect common C++ errors that the build-checkers have encountered.
    if any(map(lambda ext: filename.endswith(ext), ['.cpp', '.h', '.inl'])):
        num = 0
        for line in data.splitlines():
            num += 1
            if check_file_content.cpp_include_backslash_pattern.search(line):
                print(f'   {filename}:{num} - Backslash in #include')
                return 1
            if check_file_content.cpp_throw_std_exception_pattern.search(line):
                print(f'   {filename}:{num} - std::exception thrown')
                return 1

    return 0
check_file_content.cpp_include_backslash_pattern = re.compile('^\\s*\\#\\s*include\\s*[\\"\\<][^\\"\\>]*\\\\', re.MULTILINE)
check_file_content.cpp_throw_std_exception_pattern = re.compile(r'\bthrow\s+(std\s*::\s*)?exception\s*\(')


def check_content(files):
    '''Check content of files.

    This only applies to files meeting the conditions:
        1. Filename has certain extensions
        2. The content can be read
        3. It's a text file

    We check that:
        1. It does non contain "DO NOT COMMIT" (case insensitive)
        2. It does not contain tab
        3. For C / C++ source files:
            a. It has no missing newline at the end
            b. It has no backslash in #include
            c. It does not throw std::exception

    '''

    retval = 0
    for filename in files:
        # Skip file if extension is not in the checked list
        if not any([filename.endswith(checked_ext)
                    for checked_ext in CHECKED_EXTS]):
            continue

        # NOTE: ignored_files / ignored_exts / ignored_patterns not used

        try:
            data = Path(filename).read_text()
        except Exception as exc:
            print(exc)
            continue

        # Skip binary file
        if '\0' in data:
            continue

        print(f'  Checking file {filename}')
        retval += check_file_content(filename, data)

    return retval


def commit_hook(merge=False):
    retval = 0
    files = get_commit_files()

    print(' Auto remove trailing white space ...')
    remove_trailing_white_space(files['M'])
    remove_trailing_white_space(files['A'], new_files=True)

    print(' Check username ...')
    retval += check_username()

    if merge:
        print(' Check do not merge ...')
        retval += check_do_not_merge(files['M'])
        retval += check_do_not_merge(files['A'], new_files=True)
    else:
        print(' Check filenames ...')
        retval += check_filenames(files['M'] + files['A'])

        print(' Check content ...')
        retval += check_content(files['M'] + files['A'])

    return retval
