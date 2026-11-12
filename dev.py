"""Local development entry point, with an optional real-browser smoke check."""
import argparse
import os


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Run the browser acceptance check against an isolated demo workspace.')
    parser.add_argument('--fresh', action='store_true', help='Start a new rehearsal workspace without deleting existing history.')
    args = parser.parse_args()
    if args.check:
        from qa_browser import main
        main()
    else:
        if args.fresh:
            import tempfile
            os.environ['LEDGERFLOW_DATA'] = tempfile.mkdtemp(prefix='ledgerflow-rehearsal-')
            print('Fresh rehearsal workspace:', os.environ['LEDGERFLOW_DATA'], flush=True)
        import uvicorn
        uvicorn.run('app:app', host=os.environ.get('HOST', '127.0.0.1'),
                    port=int(os.environ.get('PORT', '8000')), workers=1)
