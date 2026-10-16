# oloop client app — brief

You are building a small native client app for **oloop**, a local cross-project tracker for open todos, questions, decisions, features and bugs. The data lives as plain Markdown files with YAML frontmatter in a folder the user chooses. A desktop CLI already reads and writes the same files, so this app must follow the file format exactly. There is no server, no account and no network access.

The target platform is: **{{TARGET PLATFORM — for example "Android (Kotlin, Jetpack Compose)" or "iOS (Swift, SwiftUI)"}}**.

Everything below is platform neutral except the section "Platform notes".

## What the app does

1. **Settings:** the user picks the data root folder once. The app remembers it and reopens it on the next start. The settings also let the user change the folder later and enter their **user name** (see "Actor" below).
2. **Project list:** every direct subfolder of the root that contains a `project.md` is a project. Show the project name, the number of open tasks and the most recent `updatedAt` across its tasks.
3. **Task list:** selecting a project opens its tasks. The project name is shown in a header. Closed tasks are hidden by default, with a switch to show them.
4. **Grouping:** the user can group the task list by one of: none, creation date, modification date, task type, task status. See "Grouping and sorting".
5. **Task detail:** selecting a task shows all of it: type, title, status, labels, description (rendered Markdown), comments, `createdAt`/`createdBy`, `updatedAt`/`updatedBy`, and the image if `imagePath` is set. Title, description and labels are editable in place. The user can close or reopen the task and add a comment.
6. **New task:** create a task in the current project (type, title, optional description and labels).

Out of scope for this version: creating or renaming projects, deleting anything, editing task type, image capture, search, notifications, any sync logic of its own. Do not invent features beyond this list.

## Data contract

The files are specified by the included material. In this order of authority:

1. The JSON Schemas (`project.schema.json`, `task.schema.json`) decide the structure.
2. The file format section of the spec explains the layout and the rules.
3. The fixtures show correct and incorrect data with the expected result.

Rules that the schemas cannot express:

- The task file name is `<task-id>.md` and must equal the `id` inside. A mismatch is an error.
- A task's `projectId` must match the `id` in the `project.md` of its folder.
- `formatVersion` is an integer, currently `1`. A missing value means 1. A higher value than the app knows must be refused with a clear message and the file must not be written.
- Timestamps are strings in the form `2026-10-16T12:00:00Z` (UTC). Keep them as strings. Do not let the YAML parser convert them to date objects, or the value changes on the next write.
- The Markdown body of a task file is the `description`.
- Frontmatter keys are written in the order shown in the spec, always including `formatVersion: 1` first. Unknown keys are not allowed.
- Invalid files must not crash the app. Show them in the list as unreadable with the reason, and do not write to them.

## Behavior rules

- **Create task:** new UUID4 as `id` and file name, `status: open`, `imagePath: null`, `createdAt` = `updatedAt` = now (UTC), `createdBy` = `updatedBy` = the actor (see below), labels trimmed, empty ones dropped, duplicates removed.
- **Edit** (title, description, labels): set `updatedAt` = now and `updatedBy` = the actor. Never change `id`, `createdAt`, `createdBy` or the file name, even when the title changes.
- **Close / reopen:** flip `status` between `open` and `closed`, set `updatedAt` and `updatedBy`. Closing is the only way to finish a task. **There is no delete anywhere.**
- **Comment:** append `{body, createdAt}` to `comments` and set `updatedAt` and `updatedBy`. Comments cannot be edited or removed.
- **Actor:** the user's name, entered in the settings and stored on the device. It is written to `createdBy` and `updatedBy`. It is free text. Until it is set, the app asks for it before the first write and does not write anything without it.
- **Closed status labels** (display only, the data stays `open`/`closed`): todo → "Done", question → "Answered", decision → "Decided", feature → "Implemented", bug → "Fixed".
- **Writing:** write to a temporary file and then replace the target, so a crash never leaves a half-written file. If the platform makes that impossible, say so and write as safely as it allows. Before overwriting an existing file, copy it to `<project>/.backup/<file name>.bak` (one generation).
- **Concurrent changes:** there is no locking, last writer wins. Before every write, re-read the file. If its `updatedAt` differs from what the app loaded, do not overwrite silently. Show the conflict and let the user choose to reload or overwrite.

## Grouping and sorting

Within a group, sort by `updatedAt`, newest first.

- **None:** one flat list.
- **Creation date:** buckets by `createdAt`: Today, Yesterday, This week, This month, Older.
- **Modification date:** the same buckets by `updatedAt`. The point of this view is to see what has been quiet for a long time, so also show the age ("5 days") on each row.
- **Task type:** one group per type in the order todo, question, decision, feature, bug.
- **Task status:** Open first, then Closed (using the type-specific closed label on the row).

Each row shows the title, the type, the labels and the age since `updatedAt`.

## UI layout

Use the platform's usual navigation. On a phone this is a stack: project list → task list → task detail. On a tablet or a wide screen use a split view with the lists on the left and the detail on the right. The header of the task list shows the project name and holds the grouping control and the show-closed switch. Editing happens directly in the detail view, with an explicit save action. Unsaved changes must not be lost silently when the user navigates away.

Keep the look plain and fast to scan: a dense list, clear type markers, no decorative elements.

## Platform notes

Read the section for the target platform. Ignore the other.

**Android.** The app has no normal file path into a user-chosen folder, and the folder may live on Google Drive. Use the Storage Access Framework: let the user pick the root with `ACTION_OPEN_DOCUMENT_TREE`, persist the permission with `takePersistableUriPermission`, and access files through `DocumentFile`/`DocumentsContract`. Paths like `/storage/...` will not work. Writing through this API is slower and not atomic by itself, so write the temporary file next to the target and rename, where the provider supports it, and otherwise write directly after the backup copy. Parse YAML with a library configured to keep timestamps as strings (for example SnakeYAML).

**iOS.** Let the user pick the root with `UIDocumentPickerViewController` (folder mode) and keep access through a security-scoped bookmark. Wrap every read and write in `startAccessingSecurityScopedResource`/`stopAccessingSecurityScopedResource` and use `NSFileCoordinator` for access to files in cloud folders. Files may not be downloaded yet, so request the download before reading. Parse YAML with a library that keeps timestamps as strings.

## How to deliver

1. State your assumptions and any rule above you could not fulfil on this platform.
2. Write the parser and writer first, as a separate module without UI.
3. Write automated tests that run **all fixtures**: each valid case must produce exactly the expected result, and each invalid case must fail for the stated reason. Add a round-trip test: reading a file and writing it back unchanged must not alter any value.
4. Then build the UI on top of that module.
5. Keep dependencies few. Name every library you use and why.
