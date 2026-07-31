# B17i Import Trigger

This same-repository pull request exists only to trigger the observable, verified B17i source import workflow.

The workflow reads `b17i_source.tar.xz` from `main`, validates its SHA256 and the project manifest, restores the complete source tree, removes all temporary bootstrap files, and commits the verified candidate back to `main`.
