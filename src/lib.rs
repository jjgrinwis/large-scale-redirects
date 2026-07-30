use std::fs::File;
use std::io::{BufReader, Read};
use std::str::from_utf8;
use std::sync::OnceLock;
use spin_sdk::http::{IntoResponse, Request, Response};
use spin_sdk::http_component;

#[http_component]
fn handle(request: Request) -> anyhow::Result<impl IntoResponse> {
    let sources = SOURCES.get().unwrap();
    let path = request.path_and_query().unwrap_or("/");

    match sources.get(path) {
        Some(index) => {
            let targets = TARGETS.get().unwrap();
            let redirect = targets.decoder().run(index as usize);

            // If the redirect target ends in " <status code>", we need to parse the status code
            let (target, status_code) = if redirect.len() > 4 && redirect[redirect.len() - 4] == b' ' {
                let code = from_utf8(&redirect[redirect.len() - 3..])
                    .unwrap()
                    .parse::<u16>()
                    .unwrap();
                (redirect[0..redirect.len() - 4].to_vec(), code)
            } else {
                (redirect, *DEFAULT_STATUS_CODE.get().unwrap())
            };

            let location = String::from_utf8_lossy(&target);
            Ok(Response::builder()
                .status(status_code)
                .header("Location", location.as_ref())
                .build())
        }
        None => {
            Ok(Response::builder()
                .status(404)
                .build())
        }
    }
}

static TARGETS: OnceLock<fcsd::Set> = OnceLock::new();
static SOURCES: OnceLock<fst::Map<Vec<u8>>> = OnceLock::new();
static DEFAULT_STATUS_CODE: OnceLock<u16> = OnceLock::new();

#[export_name = "wizer.initialize"]
pub extern "C" fn init() {
    let mut args = String::new();
    std::io::stdin()
        .read_line(&mut args)
        .expect("failed to read stdin");
    let args = args.trim().split_whitespace().collect::<Vec<_>>();
    match args[..] {
        [sources_path, targets_path, default_status_code] => {
            let default_status_code = match default_status_code.parse::<u16>() {
                Ok(code) if (301..400).contains(&code) => code,
                _ => panic!("Invalid default status code '{default_status_code}'"),
            };
            println!("Using default status code {default_status_code}");
            DEFAULT_STATUS_CODE.set(default_status_code).unwrap();
            
            println!("Loading redirect sources from {sources_path}");
            let mut sources_file =
                File::open(sources_path).expect("Unable to read encoded redirect sources");
            let size = sources_file.metadata().unwrap().len();
            let mut sources_bytes = vec![0; size as usize];
            sources_file.read_exact(&mut sources_bytes).unwrap();
            let sources_fst = fst::Map::new(sources_bytes).unwrap();
            SOURCES.set(sources_fst).unwrap();

            println!("Loading redirect targets from {targets_path}");
            let targets_file =
                File::open(targets_path).expect("Unable to read encoded redirect targets");
            let reader = BufReader::new(targets_file);
            let set = fcsd::Set::deserialize_from(reader).unwrap();
            let _ = TARGETS.set(set);
            return;
        }
        _ => {}
    }
    panic!("Expected three arguments: <sources.fst> <targets.fcsd> <default status code>");
}
