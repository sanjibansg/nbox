package main

import (
	"bytes"
	"fmt"
	"net/http"
	"os"
	"path/filepath"

	"github.com/howeyc/fsnotify"
)

//
var watcher *fsnotify.Watcher

// album represents data about a record album.
type echo struct {
	data string `json:"data"`
}

// main
func main() {

	fmt.Println("Starting watcher...")

	// creates a new file watcher
	watcher, _ = fsnotify.NewWatcher()
	defer watcher.Close()

	// starting at the root of the project, walk each file/directory searching for
	// directories
	fp, err := filepath.Abs("./../nbox")
	if err != nil {
		fmt.Println(err)
		os.Exit(1)
	}

	if err := filepath.Walk(fp, watchDir); err != nil {
		fmt.Println("ERROR", err)
	}

	fp_src, err := filepath.Abs("./source")
	if err != nil {
		fmt.Println(err)
		os.Exit(1)
	}

	if err := filepath.Walk(fp_src, watchDir); err != nil {
		fmt.Println("ERROR", err)
	}

	//
	done := make(chan bool)

	//
	go func() {
		for {
			select {
			// watch for events
			case event := <-watcher.Event:
				fmt.Printf("EVENT! %#v\n", event)
				// echo struct as data with string "hello"
				resp, err := http.Post(
					"http://0.0.0.0:6942",
					"application/json",
					bytes.NewBuffer([]byte(`{"data":"hello"}`)),
				)

				if err != nil {
					fmt.Println(err)
				}

				fmt.Println(resp)

			// watch for errors
			case err := <-watcher.Error:
				fmt.Println("ERROR", err)
			}
		}
	}()

	<-done
}

// watchDir gets run as a walk func, searching for directories to add watchers to
func watchDir(path string, fi os.FileInfo, err error) error {

	// since fsnotify can watch all the files in a directory, watchers only need
	// to be added to each nested directory
	if fi.Mode().IsDir() {
		return watcher.Watch(path)
	}

	return nil
}
