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
 * /auth route: if `?token=` is in the URL, store and redirect. Otherwise show
 * a paste-the-token field. Visit this once on each device.
 */
export function AuthGate() {
  const [params] = useSearchParams();
  const setToken = useUI((s) => s.setToken);
  const navigate = useNavigate();
  const [value, setValue] = useState("");

  useEffect(() => {
    const t = params.get("token");
    if (t) {
      setToken(t);
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
            https://&lt;mac-ip&gt;:8765/auth?token=&lt;x&gt;
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
