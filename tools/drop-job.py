#!/usr/bin/env python3
"""Remove launchd jobs from an iOS root image.

    sudo drop-job.py /Volumes/iOSRoot com.apple.mobileactivationd [more labels]

Bring-up tool: a daemon that can only crash-loop is worse than an absent one
(it burns the single CPU and floods the console), and for `mobileactivationd`
we cannot keep a patched binary because touching __TEXT makes the kernel
invalidate the page (`CODE SIGNING: cs_invalid_page` -> `Text page corruption
detected` -> the process dies wherever it next executes there).  Dropping the
job answers the other half of the question: does SpringBoard need the
activation state, or only suffer from the churn?

Same edit as the CommCenter removal in sweet-root-tweaks.py: /System/Library/
xpc/launchd.plist, job entries keyed by name, `Label` holds the identifier.
"""
import plistlib
import os
import sys

PLIST = 'System/Library/xpc/launchd.plist'


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    root = sys.argv[1]
    want = set(sys.argv[2:])
    path = os.path.join(root, PLIST)
    st = os.stat(path)
    with open(path, 'rb') as f:
        plist = plistlib.load(f)
    jobs = plist['LaunchDaemons']
    drop = [k for k, j in jobs.items() if j.get('Label') in want]
    if not drop:
        sys.exit(f'no job with those labels in {path}')
    for k in drop:
        del jobs[k]
        print(f'dropped {k}')
    with open(path, 'wb') as f:
        plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY)
    os.chown(path, st.st_uid, st.st_gid)
    os.chmod(path, st.st_mode & 0o7777)


if __name__ == '__main__':
    main()
