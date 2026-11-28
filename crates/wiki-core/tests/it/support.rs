//! Helpers shared by the tests in this binary.

/// The result a fallible test returns.
pub(crate) type TestResult<T = ()> = anyhow::Result<T>;
