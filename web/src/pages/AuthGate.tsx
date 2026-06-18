import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Container,
  Heading,
  Input,
  Stack,
  Text,
} from "@chakra-ui/react";

import { useUI } from "../stores/ui";

/**
 * /auth route: the install link carries the token in the URL *fragment*
 * (`/auth#token=...`), which browsers never send to the server — so it can't
 * land in access logs. We read it, stash it, strip it from the URL, and
 * redirect. `?token=` is still accepted for older links. Otherwise show a
 * paste-the-token field. Visit this once on each device.
 */
export function AuthGate() {
  const [params] = useSearchParams();
  const setToken = useUI((s) => s.setToken);
  const navigate = useNavigate();
  const [value, setValue] = useState("");

  useEffect(() => {
    const fromHash = new URLSearchParams(
      window.location.hash.replace(/^#/, ""),
    ).get("token");
    const t = fromHash ?? params.get("token");
    if (t) {
      setToken(t);
      // Drop the token from the URL so it isn't kept in history.
      window.history.replaceState(null, "", window.location.pathname);
      navigate("/", { replace: true });
    }
  }, [params, setToken, navigate]);

  const submit = () => {
    if (!value.trim()) return;
    setToken(value.trim());
    navigate("/", { replace: true });
  };

  return (
    <Container maxW="md" py={10}>
      <Stack gap={4}>
        <Heading size="md">agent-harness</Heading>
        <Text fontSize="sm" color="fg.muted">
          Paste your auth token, or open the install link from your Mac:
          <Box as="code" px={1}>
            https://&lt;mac&gt;.&lt;tailnet&gt;.ts.net/auth#token=&lt;x&gt;
          </Box>
        </Text>
        <Input
          placeholder="token"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          fontSize="16px"
          autoFocus
        />
        <Button onClick={submit} colorPalette="blue">
          Continue
        </Button>
      </Stack>
    </Container>
  );
}
