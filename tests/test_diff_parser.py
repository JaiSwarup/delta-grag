from __future__ import annotations

from src.ingestion.diff_parser import (
    collect_changed_file_paths,
    collect_changed_lines_by_file,
    parse_unified_diff,
)


def test_collect_changed_file_paths_multiple_files() -> None:
    diff_text = """\
diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,3 @@
 def a():
-    return 1
+    value = 2
+    return value
diff --git a/src/b.py b/src/b.py
index 3333333..4444444 100644
--- a/src/b.py
+++ b/src/b.py
@@ -10,2 +10,2 @@
-foo()
+bar()
"""
    paths = collect_changed_file_paths(diff_text)
    assert paths == ("src/a.py", "src/b.py")


def test_collect_changed_lines_by_file_includes_added_and_context_new_lines() -> None:
    diff_text = """\
diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,4 +1,5 @@
 def a():
-    return 1
+    value = 2
+    return value
 x = 10
 y = 20
"""
    changed = collect_changed_lines_by_file(diff_text)

    # Hunk new-side line progression:
    # 1: "def a():" (ctx)
    # 2: "+ value = 2" (add)
    # 3: "+ return value" (add)
    # 4: "x = 10" (ctx)
    # 5: "y = 20" (ctx)
    assert changed == {"src/a.py": (1, 2, 3, 4, 5)}


def test_parse_unified_diff_handles_new_file_and_dev_null() -> None:
    diff_text = """\
diff --git a/dev/null b/src/new_file.py
new file mode 100644
--- /dev/null
+++ b/src/new_file.py
@@ -0,0 +1,3 @@
+def created():
+    return 42
+
"""
    parsed = parse_unified_diff(diff_text)
    assert len(parsed.files) == 1

    file_diff = parsed.files[0]
    assert file_diff.is_new_file is True
    assert file_diff.old_path == "/dev/null"
    assert file_diff.new_path == "src/new_file.py"
    assert parsed.changed_files == ("src/new_file.py",)
    assert parsed.changed_lines_by_file == {"src/new_file.py": (1, 2, 3)}


def test_parse_unified_diff_rename_metadata() -> None:
    diff_text = """\
diff --git a/src/old_name.py b/src/new_name.py
similarity index 97%
rename from src/old_name.py
rename to src/new_name.py
--- a/src/old_name.py
+++ b/src/new_name.py
@@ -1,2 +1,2 @@
-def f(): pass
+def f(): return 1
"""
    parsed = parse_unified_diff(diff_text)
    assert len(parsed.files) == 1

    f = parsed.files[0]
    assert f.is_rename is True
    assert f.rename_from == "src/old_name.py"
    assert f.rename_to == "src/new_name.py"
    assert f.path == "src/new_name.py"
    assert parsed.changed_files == ("src/new_name.py",)


def test_parse_unified_diff_multiple_hunks_same_file_line_union_sorted_unique() -> None:
    diff_text = """\
diff --git a/src/multi.py b/src/multi.py
index abcdef0..1234567 100644
--- a/src/multi.py
+++ b/src/multi.py
@@ -1,3 +1,3 @@
 a = 1
-b = 2
+b = 20
 c = 3
@@ -10,2 +10,3 @@
 x = 1
-y = 2
+y = 22
+z = 33
"""
    parsed = parse_unified_diff(diff_text)
    assert parsed.changed_files == ("src/multi.py",)

    # Hunk1 touched new lines: 1,2,3
    # Hunk2 touched new lines: 10,11,12
    assert parsed.changed_lines_by_file["src/multi.py"] == (1, 2, 3, 10, 11, 12)


def test_collect_changed_file_paths_empty_diff() -> None:
    assert collect_changed_file_paths("") == ()
    assert collect_changed_lines_by_file("") == {}
