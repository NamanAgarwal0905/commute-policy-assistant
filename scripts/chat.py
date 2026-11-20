
import getpass
import httpx

BASE_URL = "http://localhost:8000"


def login(client):
    print("\n=== Commute Policy Assistant ===")
    username = input("Employee username: ").strip()
    password = getpass.getpass("Password: ")

    response = client.post(
        f"{BASE_URL}/auth/token",
        json={"username": username, "password": password}
    )

    if response.status_code != 200:
        print("Login failed:", response.text)
        return None

    token = response.json()["access_token"]
    print("\nLogin successful!")
    print("Company:", username.split("_")[0].title())
    return token


def chat():
    with httpx.Client(timeout=30) as client:
        token = login(client)

        if not token:
            return

        headers = {"Authorization": f"Bearer {token}"}

        print("\nAsk me anything about your company's commute policy.")
        print("Type 'exit' to quit.\n")

        while True:
            question = input("You: ").strip()

            if question.lower() in ("exit", "quit"):
                print("Goodbye!")
                break

            if not question:
                continue

            try:
                response = client.post(
                    f"{BASE_URL}/questions",
                    headers=headers,
                    json={"question": question}
                )

                if response.status_code == 503:
                    print("\nAssistant: Service temporarily unavailable.")
                    print("Please try again.\n")
                    continue

                response.raise_for_status()
                result = response.json()

                print(f"\nAssistant ({result['status']}):")
                print(result["answer"])

                if result.get("policy_version"):
                    print("Policy version:", result["policy_version"])

                citations = result.get("citations", [])
                if citations:
                    print("\nSources:")
                    for citation in citations:
                        print(
                            f"- {citation['document']} | "
                            f"Section: {citation['section']}"
                        )

                print()

            except httpx.HTTPStatusError as exc:
                print("\nAPI error:", exc.response.status_code)
                print(exc.response.text)

            except httpx.RequestError as exc:
                print("\nConnection error:", exc)


if __name__ == "__main__":
    chat()
