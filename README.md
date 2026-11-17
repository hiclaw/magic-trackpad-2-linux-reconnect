# Apple Magic Trackpad 2 won't reconnect on Linux after reboot

A Magic Trackpad 2 (A1535, USB `05ac:0265`) pairs with BlueZ and works — until
you reboot. After that it still sits in the paired-devices list, but Connect
does nothing. Wired USB works. macOS works. Bluetooth reconnection is dead until
you take it back to a Mac and redo the pairing there, which is a lot of work for
a trackpad.

This repository explains why and contains a script that fixes it with the
Lightning cable, no Mac required.

Tested on Arch Linux, kernel 7.2.15, BlueZ 5.87, MediaTek MT7922, trackpad
firmware `0x0318`. The fix survived reboots and power cycles.

## Symptoms

- The trackpad shows up under paired devices; clicking Connect does nothing.
- `bluetoothctl connect <address>` fails:
  ```
  Failed to connect: org.bluez.Error.Failed br-connection-create-socket
  ```
- `journalctl -u bluetooth` shows a page timeout:
  ```
  bluetoothd[...]: profiles/input/device.c:control_connect_cb() connect to <address>: Host is down (112)
  ```
- `bluetoothctl pair <address>` ends with `AuthenticationCanceled`, then
  `ConnectionAttemptFailed`.
- Removing the pairing on the Linux side does not help — the trackpad does not
  advertise, and a scan finds nothing.
- A Bluetooth mouse or keyboard on the same adapter reconnects fine, so this is
  not the controller.

## Why it happens

The trackpad is a single-host device. It does not re-pair on its own and it
does not advertise while it believes it already has a host.

The host address is stored in a USB HID feature report (`0x35`). macOS writes
that report over the Lightning cable as part of pairing. BlueZ has no such
step: it pairs over Bluetooth only, which gives the trackpad a link key but
never updates the host address it uses when it wakes up.

The result: the trackpad wakes, pages the host address it still holds from its
last USB pairing — on our unit, the Mac — and ignores the Linux machine. The
Linux host pages it, gets a page timeout, and the loop never closes. Plugging it
into the MacBook to "reset" it worked because it re-pointed the trackpad at a
host that then dropped it, but it also overwrote the address again. That is why
the ritual had to be repeated.

BlueZ 5.87 ignores link keys written over USB on firmware `0x0318`, so the
address write is the part that matters. The script still seeds a key on both
sides, which is what macOS's own flow does.

## The fix

Requirements: BlueZ, the in-kernel `hid_magicmouse` driver (stock on Arch), and
a Lightning cable that carries data.

1. Plug the trackpad into the machine.
2. Run:

   ```sh
   sudo python3 mt2-reconnect.py
   ```

3. When it asks, unplug the cable, switch the trackpad off, wait three seconds,
   switch it on.
4. Wait. It connects within a minute or two, pairs, and is bonded.
5. Reboot once to confirm.

The script writes the local adapter address into the trackpad's host report,
seeds the BlueZ device record with a locally generated link key (never
printed), restarts BlueZ, and waits for the trackpad to connect. It talks only
to the trackpad and the local BlueZ state; nothing goes over the network.

To keep it around as a command, copy it to `~/tools/trackpad-fix` and
`chmod +x` it. `trackpad-fix --status` prints the adapter, the trackpad, the
host address currently stored in the trackpad, and the BlueZ properties
without changing anything — useful for telling "wrong host address" apart from
"BlueZ lost the bond". Running it again when the trackpad is already connected
exits without touching the pairing unless you pass `--force`.

On hosts without `hid_magicmouse`, or if you want the full multitouch report
declared in the SDP cache, the upstream project below has the extra tools.

## Rules to keep it working

- **Never plug the trackpad into a Mac again.** It re-pairs over the cable and
  overwrites the host address. This is the whole bug.
- **Do not pair it from the Bluetooth menu alone.** It will work until the next
  reboot and then fail the same way.
- Charge it from the Linux machine or a plain USB charger.

## If it breaks again

Rerun the script. No Mac needed. A reboot, a BlueZ upgrade, or the trackpad's
own forget sequence can all put it back in the broken state.

## Credits

- [`philpax/magic-trackpad-2-linux-userspace`](https://github.com/philpax/magic-trackpad-2-linux-userspace)
  (MIT) documents the USB feature reports, the address write, and an SDP cache
  patch for hosts that lack `hid_magicmouse`. This script replicates only the
  part needed for a normal reconnect.
- BlueZ link-key storage in `/var/lib/bluetooth/<adapter>/<device>/info` is
  documented in the BlueZ source.

## License

MIT. See [LICENSE](LICENSE).

---

# Apple 妙控板 2 在 Linux 重启后连不上？一根线修好

妙控板 2（A1535，USB `05ac:0265`）在 BlueZ 下能配对、能用，但重启一次就废了：
配对列表里还在，点连接没反应。有线能用，macOS 能用，只有蓝牙重连不行。原办法是
拿回 Mac 走一遍有线配对再“忘记设备”，太麻烦。

实测环境：Arch Linux，内核 7.2.15，BlueZ 5.87，MT7922，触控板固件 `0x0318`。
修好后重启、断电重启都正常。

## 现象

- 已配对列表里看得到，点连接无反应。
- `bluetoothctl connect <地址>` 报
  `org.bluez.Error.Failed br-connection-create-socket`。
- `journalctl -u bluetooth` 里是寻呼超时：`Host is down (112)`。
- `bluetoothctl pair` 先是 `AuthenticationCanceled`，再是
  `ConnectionAttemptFailed`。
- 在 Linux 侧删掉配对也没用：触控板不广播，扫描扫不到。
- 同一适配器上的蓝牙键鼠正常，所以不是控制器的问题。

## 原因

妙控板是单主机设备：它认准一个主机，不主动重新配对，也不会在“自认为已有主机”时广播。

主机地址存在 USB HID 特性报告 `0x35` 里，macOS 在用数据线配对时写进去。BlueZ 没这一步：
它只走蓝牙配对，能给触控板一个链路密钥，却从不更新触控板醒来时要找的主机地址。

于是重启后，触控板去寻呼它上次从 USB 拿到的主机地址（我们这台是 Mac），对 Linux 这边
的寻呼不理；Linux 一直寻呼超时。之前“接 Mac、忘记设备”之所以能恢复，是因为又把触控板
引向了另一个主机；而插 Mac 同时也把地址改回了 Mac，所以这招每次都得重来。

固件 `0x0318` 会忽略通过 USB 写入的链路密钥，所以真正起作用的是地址写入。脚本仍然会在
两侧各放一把本地生成的密钥，和 macOS 的做法一致。

## 步骤

需要 BlueZ、内核自带 `hid_magicmouse`（Arch 默认有）、一根能传数据的 Lightning 线。

1. 用线把触控板接到电脑。
2. 运行 `sudo python3 mt2-reconnect.py`。
3. 按提示拔线、关触控板、等三秒、再打开。
4. 等一两分钟，它会自己连上并完成配对。
5. 重启一次确认。

脚本会把本机蓝牙适配器地址写进触控板，在 BlueZ 设备记录里放一把本地生成的链路密钥
（不会打印），重启蓝牙服务，然后等触控板连上来。只在触控板和本机 BlueZ 之间操作，
不联网。

想长期留着用，把脚本拷成 `~/tools/trackpad-fix` 并 `chmod +x`。`trackpad-fix --status`
只读：打印适配器、触控板、触控板里存的主机地址和 BlueZ 的属性，能分清是“主机地址不对”
还是“BlueZ 丢了配对”。已经连着的时候再跑，不改配对就退出；确实要重做加 `--force`。

内核没有 `hid_magicmouse`，或者想补 SDP 缓存里的完整多点触控报告，用下面上游项目的工具。

## 要守的两条

- **别再插 Mac。** 一插就重新配对并覆盖主机地址，这就是问题本身。
- **别只在蓝牙菜单里配对。** 能用一阵，下次重启同样失效。

充电插本机或普通充电头。

## 又坏了怎么办

重跑脚本，不需要 Mac。重启、蓝牙升级、触控板自己“忘记设备”，都可能让它回到坏状态。

## 致谢与许可

USB 特性报告的细节和地址写入方法来自
[`philpax/magic-trackpad-2-linux-userspace`](https://github.com/philpax/magic-trackpad-2-linux-userspace)
（MIT）。本脚本只保留正常重连需要的那部分。

MIT 许可，见 [LICENSE](LICENSE)。
