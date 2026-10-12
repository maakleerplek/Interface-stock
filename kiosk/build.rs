fn main() {
    // Element ids and accessible labels for the UI tests (tests/ui.rs);
    // release builds leave them out.
    let debug = std::env::var("PROFILE").as_deref() == Ok("debug");
    let config = || slint_build::CompilerConfiguration::new().with_debug_info(debug);
    slint_build::compile_with_config("ui/app.slint", config()).unwrap();

    // Design templates, rendered side by side by examples/templates.rs.
    if std::env::var_os("CARGO_FEATURE_TEMPLATES").is_some() {
        let out = std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap());
        for name in ["beton", "werkbank"] {
            let src = format!("ui/templates/{name}.slint");
            if std::path::Path::new(&src).exists() {
                println!("cargo:rerun-if-changed={src}");
                slint_build::compile_with_output_path(&src, out.join(format!("tpl_{name}.rs")), config())
                    .unwrap();
            }
        }
        println!("cargo:rerun-if-changed=ui/templates");
        println!("cargo:rerun-if-changed=ui/tokens.slint");
    }
}
