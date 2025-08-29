import os
import sys
from src.auth import authorize_and_save_credentials

def has_auth_json():
    auth_dir = os.path.join(os.path.dirname(__file__), 'auth')
    if not os.path.exists(auth_dir):
        return False
    for f in os.listdir(auth_dir):
        if f.endswith('.json'):
            return True
    return False

def main():
    print("\nWelcome to GeminiCLI2API!")
    print("1. Start API server")
    print("2. Authorize new Google credentials (create .json in 'auth' folder)")
    choice = input("Choose an option (1/2): ").strip()
    if choice == '1':
        if not has_auth_json():
            print("\n[ERROR] No .json credential found in 'auth' folder.")
            print("Please choose option 2 to authorize credentials before starting the API server.\n")
            sys.exit(1)
        import uvicorn
        from src.main import app
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", "5000"))
        uvicorn.run(app, host=host, port=port)
    elif choice == '2':
        print("\nEnter a project_id or a list of project_ids (separated by newlines):")
        print("Example: my-gcp-project-1\nmy-gcp-project-2\n...")
        print("Press Enter twice to finish input.")
        lines = []
        while True:
            line = input()
            if not line:
                break
            lines.append(line.strip())
        project_ids = [pid for pid in lines if pid]
        if not project_ids:
            print("No project_id entered. Exiting.")
            sys.exit(1)
        project_ids_str = "\n".join(project_ids)
        authorize_and_save_credentials(project_ids_str)
        print("\nAll credentials saved in 'auth' folder. You can now start the API server.")
    else:
        print("Invalid choice. Exiting.")
        sys.exit(1)

if __name__ == "__main__":
    main()