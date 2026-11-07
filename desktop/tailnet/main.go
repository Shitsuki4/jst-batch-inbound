// ra-tailnet：收货小助手内置的 Tailscale 入口（4.1.11 起随安装包、便携版一起发）。
//
// 用 Tailscale 开源的 tsnet 库（BSD-3-Clause，tailscale.com 模块）在本进程里加入用户自己的 tailnet，
// 不装 Tailscale 客户端、不建虚拟网卡、不需要管理员权限。只在 tailnet 地址上监听一个端口（默认 443），
// 收到的连接原样转给本机远程管理页的 Tailscale 入口（127.0.0.1:端口，TLS 由小助手自己做）。
// tsnet 只转发这里 Listen 的端口，本机收货服务 8765 等其他端口不会暴露到 tailnet。
//
// 和小助手之间的约定：
//   - 授权密钥只从环境变量 TS_AUTHKEY 读（不放命令行，免得出现在进程列表里）；为空时输出登录链接。
//   - 每 2 秒往 stdout 写一行 JSON 状态：{"state":"NeedsLogin|Starting|Running|...","auth_url":...,"ips":[...],...}
//   - stdin 关闭（小助手退出或崩溃）就立即退出，不会留下孤儿进程。
//   - 不向 Tailscale 上传运行日志（TS_NO_LOGS_NO_SUPPORT）。
package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"flag"
	"fmt"
	"io"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"tailscale.com/tsnet"
)

var version = "dev"

type state struct {
	State   string   `json:"state"`
	AuthURL string   `json:"auth_url,omitempty"`
	IPs     []string `json:"ips,omitempty"`
	DNSName string   `json:"dns_name,omitempty"`
	Tailnet string   `json:"tailnet,omitempty"`
	Listen  int      `json:"listen"`
	// HTTPS 证书：tailscale = tailnet 开了 HTTPS 证书，用 https://设备名.xxx.ts.net 访问不会有警告；
	// self-signed = 没开，浏览器首次会提示证书无效，核对 CertSHA256 后继续。
	CertMode   string `json:"cert_mode,omitempty"`
	CertSHA256 string `json:"cert_sha256,omitempty"`
	Error      string `json:"error,omitempty"`
	Version    string `json:"version"`
}

var outMu sync.Mutex

func emit(s state) {
	s.Version = version
	b, _ := json.Marshal(s)
	outMu.Lock()
	os.Stdout.Write(append(b, '\n'))
	outMu.Unlock()
}

func main() {
	os.Setenv("TS_NO_LOGS_NO_SUPPORT", "true")
	stateDir := flag.String("state-dir", "", "Tailscale 节点状态目录（必填）")
	hostname := flag.String("hostname", "receiving-assistant", "在 tailnet 里显示的设备名")
	listen := flag.Int("listen", 443, "tailnet 上监听的 TCP 端口")
	target := flag.String("target", "", "转发目标，只允许 127.0.0.1:端口")
	logout := flag.Bool("logout", false, "退出登录（从 tailnet 注销这台设备）后退出")
	showVersion := flag.Bool("version", false, "打印版本")
	flag.Parse()
	if *showVersion {
		fmt.Println(version)
		return
	}
	if *stateDir == "" {
		fail("缺少 --state-dir")
	}
	if !*logout {
		host, _, err := net.SplitHostPort(*target)
		if err != nil || host != "127.0.0.1" {
			fail("--target 必须是 127.0.0.1:端口")
		}
		if *listen < 1 || *listen > 65535 {
			fail("--listen 端口无效")
		}
	}
	if err := os.MkdirAll(*stateDir, 0o700); err != nil {
		fail("状态目录无法创建：" + err.Error())
	}

	srv := &tsnet.Server{
		Dir:      *stateDir,
		Hostname: *hostname,
		AuthKey:  strings.TrimSpace(os.Getenv("TS_AUTHKEY")),
		Logf:     func(string, ...any) {},
		UserLogf: func(format string, args ...any) { fmt.Fprintf(os.Stderr, format+"\n", args...) },
	}
	os.Unsetenv("TS_AUTHKEY")
	defer srv.Close()

	// 父进程退出时 stdin 会被关掉：跟着退出。
	go func() {
		io.Copy(io.Discard, os.Stdin)
		closing.Store(true)
		srv.Close()
		os.Exit(0)
	}()

	if err := srv.Start(); err != nil {
		fail("Tailscale 启动失败：" + err.Error())
	}
	lc, err := srv.LocalClient()
	if err != nil {
		fail("Tailscale 启动失败：" + err.Error())
	}

	if *logout {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		if err := lc.Logout(ctx); err != nil {
			fail("退出登录失败：" + err.Error())
		}
		emit(state{State: "LoggedOut"})
		return
	}

	self, err := selfSigned(*stateDir)
	if err != nil {
		fail("自签名证书生成失败：" + err.Error())
	}
	sum := sha256.Sum256(self.Certificate[0])
	fp := strings.ToUpper(hex.EncodeToString(sum[:]))
	var certDomains atomic.Value
	certDomains.Store([]string{})
	tcfg := &tls.Config{MinVersion: tls.VersionTLS12, GetCertificate: func(h *tls.ClientHelloInfo) (*tls.Certificate, error) {
		name := strings.ToLower(strings.TrimSuffix(h.ServerName, "."))
		for _, d := range certDomains.Load().([]string) {
			if name != "" && strings.EqualFold(name, d) {
				if c, err := lc.GetCertificate(h); err == nil {
					return c, nil
				}
			}
		}
		return self, nil
	}}
	raw, err := srv.Listen("tcp", fmt.Sprintf(":%d", *listen))
	if err != nil {
		fail(fmt.Sprintf("tailnet 端口 %d 监听失败：%v", *listen, err))
	}
	go serve(tls.NewListener(raw, tcfg), *target)

	last := ""
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		st, err := lc.StatusWithoutPeers(ctx)
		cancel()
		s := state{Listen: *listen, CertMode: "self-signed", CertSHA256: fp}
		if err != nil {
			s.State, s.Error = "Error", err.Error()
		} else {
			s.State, s.AuthURL = st.BackendState, st.AuthURL
			for _, ip := range st.TailscaleIPs {
				s.IPs = append(s.IPs, ip.String())
			}
			if st.Self != nil {
				s.DNSName = strings.TrimSuffix(st.Self.DNSName, ".")
			}
			if st.CurrentTailnet != nil {
				s.Tailnet = st.CurrentTailnet.Name
			}
			certDomains.Store(append([]string(nil), st.CertDomains...))
			if len(st.CertDomains) > 0 {
				s.CertMode = "tailscale"
			}
		}
		b, _ := json.Marshal(s)
		if string(b) != last {
			emit(s)
			last = string(b)
		}
		time.Sleep(2 * time.Second)
	}
}

var closing atomic.Bool

func serve(ln net.Listener, target string) {
	for {
		c, err := ln.Accept()
		if err != nil {
			if closing.Load() {
				return
			}
			emit(state{State: "Error", Error: "监听已停止：" + err.Error()})
			os.Exit(3)
		}
		go func(c net.Conn) {
			defer c.Close()
			if tc, ok := c.(*tls.Conn); ok {
				tc.SetDeadline(time.Now().Add(90 * time.Second))
				if err := tc.Handshake(); err != nil {
					return
				}
				tc.SetDeadline(time.Time{})
			}
			u, err := net.DialTimeout("tcp", target, 5*time.Second)
			if err != nil {
				return
			}
			defer u.Close()
			done := make(chan struct{}, 2)
			go func() { io.Copy(u, c); done <- struct{}{} }()
			go func() { io.Copy(c, u); done <- struct{}{} }()
			<-done
		}(c)
	}
}

func fail(msg string) {
	emit(state{State: "Error", Error: msg})
	os.Exit(2)
}

// selfSigned：状态目录里的自签名证书（10 年），第一次生成后一直用同一张，指纹不变。
func selfSigned(dir string) (*tls.Certificate, error) {
	crt, key := filepath.Join(dir, "ra-selfsigned.crt"), filepath.Join(dir, "ra-selfsigned.key")
	if c, err := tls.LoadX509KeyPair(crt, key); err == nil {
		return &c, nil
	}
	pk, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 120))
	tpl := &x509.Certificate{
		SerialNumber: serial, Subject: pkix.Name{CommonName: "收货小助手 Tailscale 入口"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().AddDate(10, 0, 0),
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &pk.PublicKey, pk)
	if err != nil {
		return nil, err
	}
	kb, err := x509.MarshalECPrivateKey(pk)
	if err != nil {
		return nil, err
	}
	if err := os.WriteFile(key, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: kb}), 0o600); err != nil {
		return nil, err
	}
	if err := os.WriteFile(crt, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o600); err != nil {
		return nil, err
	}
	c, err := tls.LoadX509KeyPair(crt, key)
	return &c, err
}
