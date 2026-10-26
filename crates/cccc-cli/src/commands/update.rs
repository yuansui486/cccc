//! This distribution is updated as a complete desktop package by its launcher.
use std::io::Write;

use anyhow::Result;

use crate::args::UpdateArgs;

pub async fn run(args: UpdateArgs) -> Result<()> {
    report(&mut std::io::stdout(), &args)
}

fn report(output: &mut impl Write, args: &UpdateArgs) -> Result<()> {
    if args.check {
        writeln!(output, "Installed core version: {}", crate::PRODUCT_VERSION)?;
    }
    writeln!(
        output,
        "一号同事由“一号同事启动器”统一管理更新，请在启动器中检查并安装更新。"
    )?;
    writeln!(
        output,
        "Updates are managed by the OneColleague launcher; no release metadata was requested and no installer was run."
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::args::ReleaseChannelArg;

    #[test]
    fn every_compatible_update_mode_reports_launcher_ownership() {
        for channel in [
            None,
            Some(ReleaseChannelArg::Stable),
            Some(ReleaseChannelArg::Rc),
        ] {
            for (check, offline) in [(false, false), (true, false), (true, true)] {
                let mut output = Vec::new();
                report(
                    &mut output,
                    &UpdateArgs {
                        channel,
                        check,
                        offline,
                    },
                )
                .expect("report");
                let output = String::from_utf8(output).expect("UTF-8");
                assert!(output.contains("一号同事启动器"));
                assert!(output.contains("no release metadata was requested"));
                assert_eq!(output.contains(crate::PRODUCT_VERSION), check);
                assert!(!output.contains("https://"));
            }
        }
    }

    #[test]
    fn update_and_check_need_no_runtime_io_driver() {
        // Without an I/O or timer driver an HTTP updater cannot run. Both CLI
        // paths must still succeed, including the non-offline check.
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .expect("runtime");
        for check in [false, true] {
            runtime
                .block_on(run(UpdateArgs {
                    channel: None,
                    check,
                    offline: false,
                }))
                .expect("launcher-managed update");
        }
    }
}
