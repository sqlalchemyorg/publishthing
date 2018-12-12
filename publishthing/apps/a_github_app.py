from .. import publishthing

thing = publishthing.PublishThing(
    github_webhook_secret='abcdefg'
)

application = thing.github_webhook


if __name__ == '__main__':
    from wsgiref.simple_server import make_server

    with make_server('', 8000, application) as httpd:
        print("Serving HTTP on port 8000...")

        # Respond to requests until process is killed
        httpd.serve_forever()
