import { Button, Heading, Stack, Text } from "@chakra-ui/react";
import { Shell } from "../components/Shell";
import { useUI } from "../stores/ui";

export function SettingsPage() {
  const setToken = useUI((s) => s.setToken);
  return (
    <Shell title="Settings">
      <Stack gap={6}>
        <section>
          <Heading size="sm" mb={2}>
            Auth
          </Heading>
          <Button
            size="sm"
            colorPalette="red"
            variant="outline"
            onClick={() => setToken(null)}
          >
            Sign out
          </Button>
        </section>
        <section>
          <Heading size="sm" mb={2}>
            More
          </Heading>
          <Text color="fg.muted">
            Projects, allowlist, notifications — coming in later steps.
          </Text>
        </section>
      </Stack>
    </Shell>
  );
}
