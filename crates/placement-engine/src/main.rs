use serde_json::json;
use std::io::{self, BufReader, Write};

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["--version"] {
        println!("agent-ee-placement {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    let events = args == ["--events"];
    let outcome = if !args.is_empty() && !events {
        Err(anyhow::anyhow!("usage: agent-ee-placement [--events | --version]"))
    } else {
        serde_json::from_reader(BufReader::new(io::stdin().lock()))
            .map_err(anyhow::Error::from)
            .and_then(|input: serde_json::Value| {
                if input["schema"] == agent_ee_placement::job::INPUT_SCHEMA {
                    agent_ee_placement::job_with_events(&input, events)
                } else if events {
                    agent_ee_placement::plan_with_events(&input)
                } else {
                    agent_ee_placement::plan(&input)
                }
            })
    };
    let (response, exit_code) = match outcome {
        Ok(plan) if plan["schema"] == agent_ee_placement::job::OUTPUT_SCHEMA => (plan, 0),
        Ok(plan) => (
            json!({"schema":"agent-ee.rust-planning-output/1", "plan":plan}),
            0,
        ),
        Err(error) => {
            let is_io = error.downcast_ref::<io::Error>().is_some()
                || error
                    .downcast_ref::<serde_json::Error>()
                    .is_some_and(|e| e.is_io());
            (
                json!({"schema":"agent-ee.rust-planning-output/1", "error":format!("{error:#}"),
                "error_type": if is_io {"PlanningProcessError"} else {"ValueError"}}),
                1,
            )
        }
    };
    let mut stdout = io::stdout().lock();
    if let Err(error) = serde_json::to_writer(&mut stdout, &response)
        .and_then(|_| stdout.write_all(b"\n").map_err(serde_json::Error::io))
    {
        eprintln!("cannot write planning response: {error}");
        std::process::exit(2);
    }
    std::process::exit(exit_code);
}
