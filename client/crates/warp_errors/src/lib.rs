//! `warp_errors` — 오류 보고 매크로.
//!
//! **AGPL 원본을 대체하는 자체 구현(MIT).** 원본은 Sentry 연동·오류 등록(`inventory`)·
//! 보고 빈도 제어(once-per-run)까지 포함한 크레이트지만, `warpui`/`warpui_core` 가 쓰는
//! 것은 두 형태뿐이다(호출부 46건 전수 확인):
//!
//! ```ignore
//! report_error!(err);                                     // 29건
//! report_error!(err_or_literal, extra: { "k" => v, .. });  // 17건
//! ```
//!
//! 그래서 여기서는 **로깅만** 한다. 텔레메트리는 pytmux 가 원하지 않는 기능이라
//! 되살릴 계획도 없다. 매크로가 받아들이는 **문법**은 원본과 같아야 하지만(호출부를
//! 고치지 않으려면) 확장 결과는 새로 썼다. PROVENANCE.md §2.

/// `report_error!` 가 찍는 로그 타깃. 로거 설정에서 이 계열만 따로 거를 수 있게 상수로 둔다.
pub const LOG_TARGET: &str = "errors::report_error";

/// 부가 필드를 ` [k=v k=v]` 꼴로 붙인다. 필드가 없으면 빈 문자열.
///
/// 매크로가 아니라 함수인 이유: 확장 결과를 작게 유지해 컴파일 시간을 아낀다.
#[doc(hidden)]
pub fn __format_fields(fields: &[(&'static str, String)]) -> String {
    if fields.is_empty() {
        return String::new();
    }
    let mut out = String::from(" [");
    for (i, (key, value)) in fields.iter().enumerate() {
        if i > 0 {
            out.push(' ');
        }
        out.push_str(key);
        out.push('=');
        out.push_str(value);
    }
    out.push(']');
    out
}

/// 오류를 로그로 보고한다.
///
/// - `report_error!(err)` — `{:#}` 로 찍으므로 `anyhow` 의 컨텍스트 체인이 함께 나온다.
/// - `report_error!(err, extra: { "k" => v })` — 부가 필드를 덧붙인다. 값 앞의 `%` 는
///   `Display`, `?` 는 `Debug`, 기호가 없으면 `Display`.
///
/// 원본과 마찬가지로 **제어 흐름을 바꾸지 않는다**(반환값 없음).
#[macro_export]
macro_rules! report_error {
    // --- 부가 필드 수집 (내부용) ---
    (@fields $vec:ident $key:literal => ? $value:expr $(, $($rest:tt)*)?) => {{
        $vec.push(($key, format!("{:?}", $value)));
        $crate::report_error!(@fields $vec $($($rest)*)?);
    }};
    (@fields $vec:ident $key:literal => % $value:expr $(, $($rest:tt)*)?) => {{
        $vec.push(($key, format!("{}", $value)));
        $crate::report_error!(@fields $vec $($($rest)*)?);
    }};
    (@fields $vec:ident $key:literal => $value:expr $(, $($rest:tt)*)?) => {{
        $vec.push(($key, format!("{}", $value)));
        $crate::report_error!(@fields $vec $($($rest)*)?);
    }};
    (@fields $vec:ident $(,)?) => {};

    // --- 실제 기록 (내부용) ---
    (@emit $rendered:expr, { $($fields:tt)* }) => {{
        #[allow(unused_mut)]
        let mut __fields: Vec<(&'static str, String)> = Vec::new();
        $crate::report_error!(@fields __fields $($fields)*);
        log::log!(
            target: $crate::LOG_TARGET,
            log::Level::Error,
            "{}{}",
            $rendered,
            $crate::__format_fields(&__fields),
        );
    }};

    // --- 공개 형태 ---
    // 고정 메시지 형태는 포맷 인자를 받지 않는다. 변수 데이터를 메시지에 끼워 넣으면
    // 오류가 묶이지 않으므로, 변하는 값은 `extra: { .. }` 로 보내라는 원본 의도를 따른다.
    ($fmt:literal, extra: { $($fields:tt)* } $(,)?) => {
        $crate::report_error!(@emit $fmt, { $($fields)* })
    };
    ($fmt:literal $(,)?) => {
        $crate::report_error!(@emit $fmt, {})
    };
    ($err:expr, extra: { $($fields:tt)* } $(,)?) => {
        $crate::report_error!(@emit format_args!("{:#}", $err), { $($fields)* })
    };
    ($err:expr $(,)?) => {
        $crate::report_error!(@emit format_args!("{:#}", $err), {})
    };
}

#[cfg(test)]
mod tests {
    #[test]
    fn formats_fields_in_order() {
        let fields = [("a", "1".to_string()), ("b", "2".to_string())];
        assert_eq!(super::__format_fields(&fields), " [a=1 b=2]");
        assert_eq!(super::__format_fields(&[]), "");
    }

    #[test]
    fn all_call_shapes_expand() {
        // 호출부에서 실제로 쓰는 네 형태가 전부 확장되는지 확인한다.
        let err = std::io::Error::other("boom");
        let name = "act";
        report_error!(err);
        report_error!("고정 메시지");
        report_error!("고정 메시지", extra: { "action" => name });
        let err2 = std::io::Error::other("boom2");
        report_error!(err2, extra: { "shown" => %name, "debugged" => ?name });
    }
}
