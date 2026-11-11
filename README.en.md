# Algorithm Contest++

[![Release](https://img.shields.io/github/v/release/YiHx/AlgorithmContestPP)](https://github.com/YiHx/AlgorithmContestPP/releases/latest)
[![MIT License](https://img.shields.io/github/license/YiHx/AlgorithmContestPP)](LICENSE)
[![CI](https://github.com/YiHx/AlgorithmContestPP/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/YiHx/AlgorithmContestPP/actions/workflows/ci.yml)

[![Moe Counter](https://count.getloli.com/get/@YiHx-AlgorithmContestPP?theme=rule34)](https://github.com/journey-ad/Moe-Counter)

[简体中文](README.md) · **English**

Algorithm Contest++ is a DGHub plugin that connects online judge verdicts to a DG-Lab Coyote. Wrong Answer (WA), Time Limit Exceeded (TLE), and Runtime Error (RE) verdicts can trigger a short pulse. With device output disabled, verdicts are still available in the terminal and the session record.

## Installation

You'll need [DGHub](http://dghub.top/) and Python 3.10 or newer on Windows.

1. Download `algorithmcontestpp-dghub.zip` from [GitHub Releases](https://github.com/YiHx/AlgorithmContestPP/releases/latest) and import it in the DGHub Plugin Center.
2. Download and unzip `Source code (zip)` from the same page. Open PowerShell in the extracted source folder and run `python -m pip install -r requirements.txt`.
3. Open the plugin settings and point the engine directory at the extracted `engine` folder.

To install from a local clone, run `.\starter\install_plugin.ps1 -Plugin ojhc_link` from the repository root, then restart DGHub.

Nowcoder, AtCoder, and Luogu require the browser extension. Codeforces works through its official API and can receive verdicts sooner with the extension. Chrome, Edge, and Firefox all use the `extension` folder from the source archive.

| Browser | How to load the extension |
| --- | --- |
| Chrome 121+ | Open `chrome://extensions`, turn on Developer mode, and select **Load unpacked**. Choose the `extension` folder. |
| Edge 121+ | Open `edge://extensions`, turn on Developer mode, and select **Load unpacked**. Choose the `extension` folder. |
| Firefox 140+ | Open `about:debugging#/runtime/this-firefox`, select **Load Temporary Add-on**, and choose `extension/manifest.json`. Reload the add-on after restarting Firefox. A permanent install requires a Mozilla-signed XPI. |

For installation help, share the repository link and your browser name with 大肥鱼 in DSH (DeepSeek Harness) and ask it to follow this guide. You can also provide the extracted source folder.

## Start a session

1. Open the **算法竞赛++** plugin settings in the DGHub Plugin Center.
2. In **Engine Launcher** (「Engine 启动器」), enter the contest URL and your username on that platform, then enable auto-start. If DGHub cannot find the engine, select the repository's `engine` folder.
3. For your first run, switch off **Device output** (「带设备输出」) before enabling the plugin. If you're using the browser extension, sign in and keep the page for the same contest open.
4. Wait for the engine to start receiving data, then make a new submission. With the Codeforces extension, the terminal reports that the submission baseline is ready. New verdicts received from the extension are marked `[fast]`.
5. To use device feedback, end the trial run with Ctrl+C and disable the plugin. Connect the device in DGHub, turn on device output, and enable the plugin again. Wait for `[device] 插件就绪` before making a new submission.

For practice on a finished contest, turn on **Virtual participation** (「虚拟参赛」) to start the session clock when the engine launches. If you close the engine terminal, changing the contest URL opens it again. The browser extension popup shows the active contest and the last successful check.

## Platform support

| Platform | Verdicts | Standings | What's been tested |
| --- | --- | --- | --- |
| Codeforces | Yes, with an optional browser fast path and official API fallback | Yes | Tested with a real device in DGHub V3 mode |
| VJudge | Yes | Yes | Public contest replay tested. Live contest testing is pending. |
| Nowcoder | Yes, via the browser extension | Not yet | Captured a new WA in a virtual contest. Tested device output with a simulated device. |
| AtCoder | Yes, via the browser extension | Yes | Tested with a simulated page. Live signed-in testing is pending. |
| Luogu | Not yet | Yes, via the browser extension | Standings only. WA events cannot trigger device feedback. |

The device tests so far used DGHub V3 mode. V4 still needs an end-to-end test. See the [test records and output limits](VALIDATION.md) for details.

## Feedback rules

Simple mode is the default. WA, TLE, RE, and extended periods without an Accepted verdict (AC) can trigger feedback. An AC resets the timer and the escalation count. For verdict feedback only, turn off feedback for extended periods without an AC (「长时间未 AC 时也反馈」). Pressure mode can also factor in standings and progress.

The default cooldown is 10 seconds across all events and 30 seconds between events of the same type. Output also follows rate limits and the caps set in the plugin and the mobile app. The engine uses earlier submissions to establish a baseline and reacts to new results.

## Command line

To see how verdict events look without using a device, replay the bundled sample contest.

```powershell
cd engine
python ojhc.py run --mock
```

To run a live session, replace the example contest URL and username with your own.

```powershell
python ojhc.py run --url https://codeforces.com/contest/2280 --handle YOUR_HANDLE --virtual
python ojhc.py sessions
python ojhc.py report --session 1
```

Use the ID from `sessions` when opening a report. For more details, see the [engine guide](engine/README.md), [browser extension guide](extension/README.md), and [design notes](DESIGN.md).

This is an independent project. It is unaffiliated with DGHub and DG-Lab. The code is available under the [MIT License](LICENSE).
