-- A separate database for tests. Receipt integrity tests that need to bypass the
-- append-only trigger run only here, never against the main database.
CREATE DATABASE campus_test;
