## Change Log

All notable changes to this project are listed here.  

Change are listed in reverse chronological order (newest to oldest).  

<span class="changelog">


###### [ 1.0.26 ] - 2024/06/10

  * Added service `zeroconf_device_connect` that calls the `addUser` Spotify Zeroconf API endpoint to issue a call to SpConnectionLoginBlob.  If successful, the associated device id is added to the Spotify Connect active device list for the specified user account.
  * Added service `zeroconf_device_disconnect` that calls the `resetUsers` Spotify Zeroconf API endpoint to issue a call to SpConnectionLogout.  The currently logged in user (if any) will be logged out of Spotify Connect, and the device id removed from the active Spotify Connect device list.
  * Removed service `zeroconf_device_resetusers` and replaced it with `zeroconf_device_disconnect`.
  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.48.

###### [ 1.0.25 ] - 2024/06/08

  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.47.

###### [ 1.0.25 ] - 2024/06/08

  * Fixed a bug that was causing `ValueError: list.remove(x): x not in list` exceptions to be raised whenever the user changed configuration options for a service.  This started appearing with the HA 2024.6.1 release.

###### [ 1.0.24 ] - 2024/06/07

  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.46.
  * Added the following requirements to manifest.json so that any dependency `ResolutionImpossible` errors can be quickly identified: 'oauthlib>=3.2.2', 'platformdirs>=4.1.0', 'requests>=2.31.0', 'requests_oauthlib>=1.3.1', 'zeroconf>=0.132.2'.  This bug bit me in the HA 2024.6.1 release when the HA devs upgraded the `requests` dependency to 2.32.3!  The System log was showing that the `spotifywebapiPython` library was the invalid dependency, but it was not - the REAL culprit was the `requests` dependency!

###### [ 1.0.23 ] - 2024/06/07

  * Re-updated underlying `spotifywebapiPython` package requirement to version 1.0.45.

###### [ 1.0.22 ] - 2024/06/07

  * Re-updated underlying `spotifywebapiPython` package requirement to version 1.0.44.

###### [ 1.0.21 ] - 2024/06/07

  * Fixed a bug that caused player state to return an error every 30 seconds when playing the Spotify DJ playlist.  As the Spotify Web API does not support retrieving the DJ playlist (`spotify:playlist:37i9dQZF1EYkqdzj48dyYq`), it simply returns a manually built representation of the Spotify DJ playlist with limited properties populated (uri, id, name, description, etc).  Note that Spotify DJ support is not supported (as of 2024/06/07) by the Spotify Web API (this includes starting play, retrieving playlist details, retrieving playlist items, etc).
  * Added service `zeroconf_discover_devices` to discover Spotify Connect devices on the local network via the ZeroConf (aka MDNS) service, and return details about each device. 
  * Added service `zeroconf_device_getinfo` to retrieve Spotify Connect device information from the Spotify Zeroconf API `getInfo` endpoint.
  * Added service `zeroconf_device_resetusers` to reset users for a Spotify Connect device by calling the Spotify Zeroconf API `resetUsers` endpoint.
  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.44.

###### [ 1.0.20 ] - 2024/06/06

  * Changed all `media_player.schedule_update_ha_state(force_refresh=True)` calls to `schedule_update_ha_state(force_refresh=False)` to improve performance.  Suggested by @bdraco, along with an explanation of why.  Thanks @bdraco!
  * Changed logic to call `session.hass.config_entries.async_update_entry` via a `hass.add_job` call instead of calling directly.  This fixes the issue of `Detected that custom integration 'spotifyplus' calls hass.config_entries.async_update_entry from a thread other than the event loop, which may cause Home Assistant to crash or data to corrupt` that was introduced with HA 2024.6 release.
  * Changed logic to access local file system files via a `hass.async_add_executor_job` call.  This fixes the issue of `Detected blocking call to open inside the event loop by custom integration 'X' ...` that was introduced with HA 2024.6 release.

###### [ 1.0.19 ] - 2024/05/03

  * Changed all `media_player.async_write_ha_state()` calls to `schedule_update_ha_state(force_refresh=True)` calls due to HA 2024.5 release requirements.  This fixes the issue of "Failed to call service X. Detected that custom integration 'Y' calls async_write_ha_state from a thread at Z. Please report it to the author of the 'Y' custom integration.".
  * Added more information to system health display (version, integration configs, etc).
  * Updated Python version from 3.11 to 3.12.3 due to HA 2024.5 release requirements.

###### [ 1.0.18 ] - 2024/04/25

  * Updated various `media_player` services that control playback to verify a Spotify Connect Player device is active.  If there is no active device, or the default device was specified (e.g. "*"), then we will force the configuration option default device to be used and transfer playback to it.  If an active device was found, then we will use it without transferring playback for services that do not specify a `deviceId` argument.  For services that can supply a `deviceId` argument, we will issue a transfer playback command if a device id (or name) was specified.

###### [ 1.0.17 ] - 2024/04/21

  * Added device name support to the following custom services that take a Spotify Connect Player `deviceId` argument for player functions.  You can now specify either a device id or device name in the `deviceId` argument to target a specific Spotify Connect Player device.  Services updated were: `player_media_play_context`, `player_media_play_track_favorites`, `player_media_play_tracks`, `player_transfer_playback`.
  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.43.

###### [ 1.0.16 ] - 2024/04/21

  * Added extra state attribute `spotifyplus_device_id` that lists the Spotify Connect Player device id that is in use.
  * Added extra state attribute `spotifyplus_device_name` that lists the Spotify Connect Player device name that is in use.
  * Refer to the [wiki documentation](https://github.com/thlucas1/homeassistantcomponent_spotifyplus/wiki/Media-Player-Service-Enhancements#state-custom-variables) page for more details about custom state variables.

###### [ 1.0.15 ] - 2024/04/05

  * Added `MediaPlayerEntityFeature.VOLUME_MUTE` support to handle volume mute requests.
  * Added `MediaPlayerEntityFeature.VOLUME_STEP` support to handle volume step requests.
  * Updated Media Browser logic to return an empty `BrowseMedia` object when ignoring Sonos-Card 'favorites' node requests, as a null object was causing numerous `Browse Media should use new BrowseMedia class` log warnings.
  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.42.

###### [ 1.0.14 ] - 2024/04/04

  * Added service `player_media_play_track_favorites` to play all track favorites for the current user.
  * Increased all browse media limits from 50 items to 150 items.
  * Updated Media Browser logic to ignore Sonos-Card 'favorites' node requests, as there is no Spotify direct equivalent.
  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.41.

###### [ 1.0.13 ] - 2024/03/28

  * Updated `_CallScriptPower` method to use the script uniqueid value (instead of the entity_id value) when calling the `turn_on` and `turn_off` scripts.

###### [ 1.0.12 ] - 2024/03/27

  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.40.
  * Added service `turn_on` and `turn_off` support for the player.  Playback control is transferred to the player after turning on.  Configuration options support the execution of scripts to allow external devices to be powered on and off.  Refer to the [wiki documentation](https://github.com/thlucas1/homeassistantcomponent_spotifyplus/wiki/Media-Player-Service-Enhancements#turn-on--off) on how to configure this feature.
  * Added support for media controls to properly function when the Spotify Connect Player loses the active device reference.  For example, when the player goes into an `idle` state due to player pausing for extended period of time, you can now resume play without having to re-select the source (avoids `No active playback device found` errors).

###### [ 1.0.11 ] - 2024/03/24

  * Updated media_player SCAN_INTERVAL to 1 second to inform HA of Spotify status updates in near real time (e.g. pause, resume, next track, etc).
  * Updated `media_player.update` logic to only call the spotifywebapiPython `SpotifyClient.GetPlayerNowPlaying` every 30 seconds OR if a player command is issued (e.g. pause, play, next / previous track, seek, volume, etc) OR if the current track is nearing the end of play (e.g. next track in a playlist or queue).
  * This update adds a few more calls to the Spotify Web API, but not many.  The trade-off is near real-time updates of player status.
  * Added service `follow_playlist` to add the current user as a follower of a playlist.
  * Added service `unfollow_playlist` to remove the current user as a follower of a playlist
  * Added service `follow_users` to add the current user as a follower of one or more users.
  * Added service `unfollow_users` to remove the current user as a follower of one or more users.

###### [ 1.0.10 ] - 2024/03/20

  * Added service `follow_artists` to add the current user as a follower of one or more artists.
  * Added service `unfollow_artists` to remove the current user as a follower of one or more artists.
  * Added service `save_album_favorites` to save one or more items to the current user's album favorites.
  * Added service `remove_album_favorites` to remove one or more items from the current user's album favorites.
  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.37.
  * Updated service `playlist_create` to add the `image_path` argument to allow a cover art image to be assigned when a playlist is created.
  * Updated service `playlist_change` to add the `image_path` argument to allow a cover art image to be updated when a playlist details are updated.

###### [ 1.0.9 ] - 2024/03/19

  * Added service `playlist_create` to create a new Spotify playlist.
  * Added service `playlist_change` to change the details for an existing Spotify playlist.
  * Added service `playlist_cover_image_add` to replace the image displayed for a specified playlist ID.
  * Added service `playlist_items_clear` to remove (clear) all items from a user's playlist.
  * Added service `playlist_items_remove` to remove one or more items from a user's playlist.
  * Added service `save_track_favorites` to save one or more items to the current user's track favorites.
  * Added service `remove_track_favorites` to remove one or more items from the current user's track favorites.
  * Updated `media_player.play_media` method to better support `play_media` service enqueue features.
  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.36.

###### [ 1.0.8 ] - 2024/03/15

  * Added service `playlist_items_add` to add one or more items to a user's playlist.  Items are added in the order they are listed in the `uris` argument.

###### [ 1.0.7 ] - 2024/03/07

  * Updated service `service_spotify_player_media_play_context` to pause the Spotify Connect device before switching play context, and resuming after.
  * Updated service `service_spotify_player_media_play_tracks` to pause the Spotify Connect device before switching play context, and resuming after.
  * Updated media_player to inform HA of manual status updates as they happen (e.g. pause, resume, next track, etc).

###### [ 1.0.6 ] - 2024/03/05

  * Updated service `player_transfer_playback` schema to make the `device_id` argument optional instead of required.  This allows the active spotify connect player to be used (if desired) when transferring playback.
  * commented out the `ignore: "brands"` in validate.yaml, as brands have been added for the integration.

###### [ 1.0.5 ] - 2024/03/02

  * Added configuration option `default_device` to allow a user to specify a default Spotify Connect device to use when one is not active.
  * Added service `player_media_play_context` to start playing one or more tracks of the specified context on a Spotify Connect device.
  * Added service `player_media_play_tracks` to start playing one or more tracks on a Spotify Connect device.
  * Added service `player_transfer_playback` to transfer playback to a new Spotify Connect device and optionally begin playback.
  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.33.

###### [ 1.0.4 ] - 2024/03/01

  * Added service `search_albums` to search the Spotify catalog for matching album criteria.
  * Added service `search_artists` to search the Spotify catalog for matching artist criteria.
  * Added service `search_audiobooks` to search the Spotify catalog for matching audiobook criteria.
  * Added service `search_episodes` to search the Spotify catalog for matching episode criteria.
  * Added service `search_shows` to search the Spotify catalog for matching show (aka podcast) criteria.
  * Added service `search_tracks` to search the Spotify catalog for matching track criteria.
  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.32.

###### [ 1.0.3 ] - 2024/02/28

  * Updated service `get_show_episodes` to include the `limit_total` argument.
  * Added service `get_player_queue_info` to retrieve the player queue information.
  * Added service `get_player_devices` to retrieve player device list.

###### [ 1.0.2 ] - 2024/02/28

  * Updated underlying `spotifywebapiPython` package requirement to version 1.0.31.

###### [ 1.0.1 ] - 2024/02/25

  * Version 1 initial release.

</span>