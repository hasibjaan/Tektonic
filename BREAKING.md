# How we would break this

## 1. Crash points table

| # | File and function (line) | What is on disk if the process dies here | What open() does |
|---|---|---|---|

To be written from evidence.

## 2. The ordering argument

To be written from evidence.

## 3. Known weaknesses

1. **Power loss not claimed; on macOS `os.fsync` doesn't flush the drive cache.** We use plain `os.fsync` (CLAUDE.md section 9). The macOS `fsync(2)` man page on our demo laptop says fsync moves data to the drive, but "the drive itself may not physically write the data … and it may be written in an out-of-order sequence", and that this can happen "if the drive loses power or the OS crashes". So after a power loss or OS crash, a commit marker could be on the disk while part of its body is not. Our guarantee covers **process death only**, which is what the brief tests. The fix would be `fcntl(fd, F_FULLFSYNC)` (available here: `F_FULLFSYNC = 51`), at a cost in write speed. Evidence: `evidence/fsync-macos.txt`.

Further weaknesses: to be written from evidence.

## 4. What we tried that failed

To be written from evidence.

## 5. If we had 24 more hours

To be written from evidence.
