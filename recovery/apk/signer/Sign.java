// SPDX-License-Identifier: MIT
import com.android.apksig.ApkSigner;
import com.android.apksig.ApkVerifier;

import java.io.File;
import java.io.FileInputStream;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.cert.X509Certificate;
import java.util.Collections;

/** Sign (and align) an APK with apksig: java -cp apksig.jar:. Sign KEYSTORE PASSFILE IN OUT */
public final class Sign {
    public static void main(String[] args) throws Exception {
        char[] pass = new String(java.nio.file.Files.readAllBytes(new File(args[1]).toPath()), "UTF-8").trim().toCharArray();
        KeyStore store = KeyStore.getInstance("PKCS12");
        try (FileInputStream in = new FileInputStream(args[0])) {
            store.load(in, pass);
        }
        String alias = store.aliases().nextElement();
        PrivateKey key = (PrivateKey) store.getKey(alias, pass);
        X509Certificate cert = (X509Certificate) store.getCertificate(alias);
        ApkSigner.SignerConfig signer = new ApkSigner.SignerConfig.Builder("A22X", key, Collections.singletonList(cert)).build();
        new ApkSigner.Builder(Collections.singletonList(signer))
                .setInputApk(new File(args[2])).setOutputApk(new File(args[3]))
                .setMinSdkVersion(26).setV1SigningEnabled(false).setV2SigningEnabled(true).setV3SigningEnabled(true)
                .build().sign();
        ApkVerifier.Result result = new ApkVerifier.Builder(new File(args[3])).setMinCheckedPlatformVersion(26).build().verify();
        if (!result.isVerified()) {
            System.err.println(result.getErrors());
            System.exit(1);
        }
        System.out.println("{\"verified\":true,\"v2\":" + result.isVerifiedUsingV2Scheme() + ",\"v3\":" + result.isVerifiedUsingV3Scheme() + "}");
    }
}
