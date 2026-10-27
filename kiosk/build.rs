fn main() {
    // Element ids and accessible labels for the UI tests (tests/ui.rs);
    // release builds leave them out.
    let debug = std::env::var("PROFILE").as_deref() == Ok("debug");
    let config = || slint_build::CompilerConfiguration::new().with_debug_info(debug);
    slint_build::compile_with_config("ui/app.slint", config()).unwrap();
}
