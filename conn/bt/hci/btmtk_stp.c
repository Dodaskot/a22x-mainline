// SPDX-License-Identifier: GPL-2.0
/*
 * Bluetooth HCI driver for MediaTek CONSYS (connac1x) behind the WMT/STP
 * stack, as found on MT6833 with the MT6631 A-die (Samsung SM-A226B).
 *
 * The BT core firmware is loaded by wmt_drv; the BT STP channel carries
 * plain H4 frames, so this only powers the function through WMT and moves
 * H4 frames between STP and hci_core.
 */
#include <linux/module.h>
#include <linux/skbuff.h>
#include <linux/workqueue.h>
#include <linux/unaligned.h>
#include <net/bluetooth/bluetooth.h>
#include <net/bluetooth/hci_core.h>

#include "wmt_exp.h"
#include "stp_exp.h"

#define BTMTK_STP_RXBUF	2048

struct btmtk_stp {
	struct hci_dev *hdev;
	struct work_struct rx_work;
	struct sk_buff *rx_skb;		/* frame being reassembled */
	unsigned int rx_need;		/* bytes still missing for the current stage */
	bool rx_in_hdr;
	u8 rx_buf[BTMTK_STP_RXBUF];
	u8 tx_buf[HCI_MAX_FRAME_SIZE + 1];
};

static struct btmtk_stp *g_bt;

/* the factory address lives in nvdata (APCFG/APRDEB/BT_Addr), not in the chip */
static char *bdaddr;
module_param(bdaddr, charp, 0444);
MODULE_PARM_DESC(bdaddr, "public BD address, e.g. 7C:C2:25:A5:6A:37");

static bool dump;
module_param(dump, bool, 0644);
MODULE_PARM_DESC(dump, "hex-dump raw STP rx/tx chunks");

static unsigned int btmtk_hdr_len(u8 type)
{
	switch (type) {
	case HCI_EVENT_PKT:	return HCI_EVENT_HDR_SIZE;
	case HCI_ACLDATA_PKT:	return HCI_ACL_HDR_SIZE;
	case HCI_SCODATA_PKT:	return HCI_SCO_HDR_SIZE;
	case HCI_ISODATA_PKT:	return HCI_ISO_HDR_SIZE;
	}
	return 0;
}

static unsigned int btmtk_payload_len(struct sk_buff *skb)
{
	switch (hci_skb_pkt_type(skb)) {
	case HCI_EVENT_PKT:
		return hci_event_hdr(skb)->plen;
	case HCI_ACLDATA_PKT:
		return __le16_to_cpu(hci_acl_hdr(skb)->dlen);
	case HCI_SCODATA_PKT:
		return hci_sco_hdr(skb)->dlen;
	case HCI_ISODATA_PKT:
		return hci_iso_data_len(__le16_to_cpu(hci_iso_hdr(skb)->dlen));
	}
	return 0;
}

/* H4 reassembly: frames may be split or packed arbitrarily by STP */
static void btmtk_stp_rx(struct btmtk_stp *bt, const u8 *data, int len)
{
	while (len > 0) {
		unsigned int n;

		if (!bt->rx_skb) {
			unsigned int hlen = btmtk_hdr_len(data[0]);

			if (!hlen) {
				bt_dev_err(bt->hdev, "unknown H4 type 0x%02x, dropping %d bytes",
					   data[0], len);
				bt->hdev->stat.err_rx++;
				return;
			}
			bt->rx_skb = bt_skb_alloc(HCI_MAX_FRAME_SIZE, GFP_KERNEL);
			if (!bt->rx_skb)
				return;
			hci_skb_pkt_type(bt->rx_skb) = data[0];
			bt->rx_need = hlen;
			bt->rx_in_hdr = true;
			data++;
			len--;
			continue;
		}

		n = min_t(unsigned int, bt->rx_need, len);
		if (bt->rx_skb->len + n > HCI_MAX_FRAME_SIZE) {
			bt_dev_err(bt->hdev, "oversized frame");
			kfree_skb(bt->rx_skb);
			bt->rx_skb = NULL;
			return;
		}
		skb_put_data(bt->rx_skb, data, n);
		data += n;
		len -= n;
		bt->rx_need -= n;
		if (bt->rx_need)
			continue;

		if (bt->rx_in_hdr) {
			bt->rx_in_hdr = false;
			bt->rx_need = btmtk_payload_len(bt->rx_skb);
			if (bt->rx_need)
				continue;
		}
		bt->hdev->stat.byte_rx += bt->rx_skb->len;
		hci_recv_frame(bt->hdev, bt->rx_skb);
		bt->rx_skb = NULL;
	}
}

static void btmtk_stp_rx_work(struct work_struct *work)
{
	struct btmtk_stp *bt = container_of(work, struct btmtk_stp, rx_work);
	int n;

	while ((n = mtk_wcn_stp_receive_data(bt->rx_buf, sizeof(bt->rx_buf),
					     BT_TASK_INDX)) > 0) {
		if (dump)
			print_hex_dump(KERN_INFO, "btstp rx: ", DUMP_PREFIX_OFFSET, 16, 1,
				       bt->rx_buf, min(n, 64), false);
		btmtk_stp_rx(bt, bt->rx_buf, n);
	}
}

static void btmtk_stp_event_cb(void)
{
	if (g_bt)
		schedule_work(&g_bt->rx_work);
}

static int btmtk_stp_open(struct hci_dev *hdev)
{
	if (mtk_wcn_wmt_func_on(WMTDRV_TYPE_BT) == MTK_WCN_BOOL_FALSE) {
		bt_dev_err(hdev, "WMT failed to power on BT");
		return -EIO;
	}
	if (mtk_wcn_stp_is_ready() == MTK_WCN_BOOL_FALSE) {
		bt_dev_err(hdev, "STP is not ready");
		mtk_wcn_wmt_func_off(WMTDRV_TYPE_BT);
		return -EIO;
	}
	mtk_wcn_stp_set_bluez(0);	/* frames go through the STP RX queue */
	mtk_wcn_stp_register_event_cb(BT_TASK_INDX, btmtk_stp_event_cb);
	return 0;
}

static int btmtk_stp_close(struct hci_dev *hdev)
{
	struct btmtk_stp *bt = hci_get_drvdata(hdev);

	mtk_wcn_stp_register_event_cb(BT_TASK_INDX, NULL);
	cancel_work_sync(&bt->rx_work);
	kfree_skb(bt->rx_skb);
	bt->rx_skb = NULL;
	mtk_wcn_wmt_func_off(WMTDRV_TYPE_BT);
	return 0;
}

static int btmtk_stp_flush(struct hci_dev *hdev)
{
	return 0;
}

/*
 * The firmware advertises Synchronization Train (LMP page 2) but answers
 * HCI_Read_Synchronization_Train_Parameters with "unknown command", which
 * aborts controller init. Answer it locally with the spec defaults.
 */
static bool btmtk_stp_is_fake_cmd(struct sk_buff *skb)
{
	return hci_skb_pkt_type(skb) == HCI_COMMAND_PKT &&
	       skb->len >= HCI_COMMAND_HDR_SIZE &&
	       get_unaligned_le16(skb->data) == HCI_OP_READ_SYNC_TRAIN_PARAMS;
}

/* Consumes @skb only on success; on error hci_send_frame() frees it. */
static int btmtk_stp_fake_cmd(struct hci_dev *hdev, struct sk_buff *skb)
{
	struct {
		struct hci_event_hdr hdr;
		struct hci_ev_cmd_complete cc;
		struct {
			u8 status;
			__le16 interval;
			__le32 timeout;
			u8 service_data;
		} __packed rp;
	} __packed ev;
	struct sk_buff *evskb;

	ev.hdr.evt = HCI_EV_CMD_COMPLETE;
	ev.hdr.plen = sizeof(ev) - sizeof(ev.hdr);
	ev.cc.ncmd = 1;
	ev.cc.opcode = cpu_to_le16(HCI_OP_READ_SYNC_TRAIN_PARAMS);
	ev.rp.status = 0;
	ev.rp.interval = cpu_to_le16(0x0080);
	ev.rp.timeout = cpu_to_le32(0x0002ee00);
	ev.rp.service_data = 0;

	evskb = bt_skb_alloc(sizeof(ev), GFP_KERNEL);
	if (!evskb)
		return -ENOMEM;	/* fails the command now instead of timing out */
	hci_skb_pkt_type(evskb) = HCI_EVENT_PKT;
	skb_put_data(evskb, &ev, sizeof(ev));
	hci_recv_frame(hdev, evskb);
	kfree_skb(skb);
	return 0;
}

/*
 * Ownership of @skb: freed here on success only. On a negative return
 * hci_send_frame() frees it, so error paths must leave it alone.
 */
static int btmtk_stp_send(struct hci_dev *hdev, struct sk_buff *skb)
{
	struct btmtk_stp *bt = hci_get_drvdata(hdev);
	int len = skb->len + 1, ret;

	if (btmtk_stp_is_fake_cmd(skb))
		return btmtk_stp_fake_cmd(hdev, skb);

	if (len > sizeof(bt->tx_buf))
		return -EMSGSIZE;
	bt->tx_buf[0] = hci_skb_pkt_type(skb);
	skb_copy_bits(skb, 0, bt->tx_buf + 1, skb->len);

	if (dump)
		print_hex_dump(KERN_INFO, "btstp tx: ", DUMP_PREFIX_OFFSET, 16, 1,
			       bt->tx_buf, min(len, 64), false);
	ret = mtk_wcn_stp_send_data(bt->tx_buf, len, BT_TASK_INDX);
	if (ret != len) {
		bt_dev_err(hdev, "STP send failed (%d of %d)", ret, len);
		hdev->stat.err_tx++;
		return -EIO;
	}
	hdev->stat.byte_tx += skb->len;
	switch (hci_skb_pkt_type(skb)) {
	case HCI_COMMAND_PKT:	hdev->stat.cmd_tx++; break;
	case HCI_ACLDATA_PKT:	hdev->stat.acl_tx++; break;
	case HCI_SCODATA_PKT:	hdev->stat.sco_tx++; break;
	}
	kfree_skb(skb);
	return 0;
}

/* MediaTek vendor command, same as btmtk_set_bdaddr() */
static int btmtk_stp_set_bdaddr(struct hci_dev *hdev, const bdaddr_t *bdaddr)
{
	struct sk_buff *skb;

	skb = __hci_cmd_sync(hdev, 0xfc1a, sizeof(bdaddr_t), bdaddr, HCI_INIT_TIMEOUT);
	if (IS_ERR(skb))
		return PTR_ERR(skb);
	kfree_skb(skb);
	return 0;
}

static int btmtk_stp_setup(struct hci_dev *hdev)
{
	bdaddr_t ba;
	u8 b[6];
	int i;

	if (!bdaddr || sscanf(bdaddr, "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx",
			      &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6) {
		bt_dev_warn(hdev, "no bdaddr= given, address left unconfigured");
		hci_set_quirk(hdev, HCI_QUIRK_INVALID_BDADDR);
		return 0;
	}
	for (i = 0; i < 6; i++)
		ba.b[i] = b[5 - i];
	return btmtk_stp_set_bdaddr(hdev, &ba);
}

static int btmtk_stp_post_init(struct hci_dev *hdev)
{
	/*
	 * MT6833/MT6631 stock GORMcmd_HCC_Set_Sleep_Timeout (0xfc7a).
	 * These seven bytes match both the stock MT6631 defaults and this
	 * board's BT_Addr sleep field. WMT's transport sleep handshake alone
	 * does not enable the Bluetooth controller's own low-power state.
	 * Apply after HCI init on every power-on: WMT function-off and HCI
	 * Reset may discard controller settings.
	 */
	static const u8 sleep_params[] = { 0x03, 0x04, 0, 0, 0, 0, 0 };
	struct sk_buff *skb;
	int err;

	skb = __hci_cmd_sync(hdev, 0xfc7a, sizeof(sleep_params), sleep_params,
			     HCI_INIT_TIMEOUT);
	if (IS_ERR(skb))
		return PTR_ERR(skb);
	err = skb->len == 1 ? -bt_to_errno(skb->data[0]) : -EPROTO;
	kfree_skb(skb);
	if (err)
		bt_dev_err(hdev, "sleep configuration failed (%d)", err);
	else
		bt_dev_info(hdev, "MT6631 sleep configuration enabled");
	return err;
}

static int __init btmtk_stp_init(void)
{
	struct btmtk_stp *bt;
	struct hci_dev *hdev;
	int err;

	bt = kzalloc(sizeof(*bt), GFP_KERNEL);
	if (!bt)
		return -ENOMEM;
	INIT_WORK(&bt->rx_work, btmtk_stp_rx_work);

	hdev = hci_alloc_dev();
	if (!hdev) {
		kfree(bt);
		return -ENOMEM;
	}
	bt->hdev = hdev;
	hdev->bus = HCI_SDIO;	/* on-SoC, closest match */
	hci_set_drvdata(hdev, bt);
	hdev->open = btmtk_stp_open;
	hdev->close = btmtk_stp_close;
	hdev->flush = btmtk_stp_flush;
	hdev->send = btmtk_stp_send;
	hdev->set_bdaddr = btmtk_stp_set_bdaddr;
	hdev->setup = btmtk_stp_setup;
	hdev->post_init = btmtk_stp_post_init;
	/* WMT function-off also discards the programmed factory address. */
	hci_set_quirk(hdev, HCI_QUIRK_NON_PERSISTENT_SETUP);

	g_bt = bt;
	err = hci_register_dev(hdev);
	if (err < 0) {
		g_bt = NULL;
		hci_free_dev(hdev);
		kfree(bt);
		return err;
	}
	return 0;
}

static void __exit btmtk_stp_exit(void)
{
	struct btmtk_stp *bt = g_bt;

	hci_unregister_dev(bt->hdev);
	g_bt = NULL;
	hci_free_dev(bt->hdev);
	kfree(bt);
}

module_init(btmtk_stp_init);
module_exit(btmtk_stp_exit);
MODULE_DESCRIPTION("MediaTek CONSYS Bluetooth over WMT/STP");
MODULE_LICENSE("GPL");
