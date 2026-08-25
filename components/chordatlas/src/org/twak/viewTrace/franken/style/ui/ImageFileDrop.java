package org.twak.viewTrace.franken.style.ui;

import java.awt.Color;
import java.awt.Dimension;
import java.awt.Graphics;
import java.awt.datatransfer.DataFlavor;
import java.awt.dnd.DnDConstants;
import java.awt.dnd.DropTarget;
import java.awt.dnd.DropTargetDropEvent;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.awt.image.BufferedImage;
import java.io.File;
import java.util.List;

import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.SwingUtilities;
import javax.swing.SwingConstants;
import javax.swing.border.LineBorder;

import org.twak.utils.Imagez;
import org.twak.utils.ui.Rainbow;
import org.twak.utils.ui.SimpleFileChooser;

public class ImageFileDrop extends JComponent {

	JLabel empty = new JLabel("style");
	volatile BufferedImage dropped;
	
	volatile boolean hover;
	volatile boolean busy;
	volatile String status;
	final String prompt;
	
	public ImageFileDrop(BufferedImage image) {
		this( image, "[drop style image]" );
	}

	public ImageFileDrop( BufferedImage image, String prompt ) {
//		super (label);
		this.prompt = prompt;
		
		setPreferredSize( new Dimension( 200,80) );
//		setHorizontalAlignment( SwingConstants.CENTER );
//		setBorder( new LineBorder( Color.black, 3 ) );
//		setOpaque( true );
//		setBackground( Color.white );
//		setForeground( Color.black );
		
		empty.setHorizontalAlignment( SwingConstants.CENTER );
		empty.setForeground( Rainbow.rainbow[4] );
		
		MouseAdapter ma = new MouseAdapter() {
			public void mouseEntered(java.awt.event.MouseEvent e) {
				hover = true;
				repaint();
			};
			public void mouseExited(java.awt.event.MouseEvent e) {
				hover = false;
				repaint();
			};
			public void mouseDragged(java.awt.event.MouseEvent e) {
				hover = true;
				repaint();
			};
			
			@Override
			public void mouseClicked( MouseEvent e ) {
				
				if (e.getButton() == 3) {
					rightClick();
				} else {
				
				new SimpleFileChooser(null, false, "select image file, or drag n drop") {
					@Override
					public void heresTheFile( File f ) throws Throwable {
						loadFile( f );
					}
				}; }
			}
		};
		
		addMouseListener( ma );
		addMouseMotionListener( ma );
		
		setDropTarget(new DropTarget() {
		    public synchronized void drop(DropTargetDropEvent evt) {
		        try {
		            evt.acceptDrop(DnDConstants.ACTION_COPY);
		            List<File> droppedFiles = (List<File>)
		                evt.getTransferable().getTransferData(DataFlavor.javaFileListFlavor);
		            if ( !droppedFiles.isEmpty() )
		            	loadFile( droppedFiles.get( 0 ) );
		        } catch (Exception ex) {
		            ex.printStackTrace();
		        }
		    }
		});
		
		setImage( image );
	}

	public synchronized void loadFile( File file ) {
		if ( file == null )
			return;
		if ( busy ) {
			setStatus( "Reference encoding is already running." );
			return;
		}
		busy = true;
		setStatus( "Encoding reference..." );
		Thread worker = new Thread( () -> {
			try {
				BufferedImage read = process( file );
				if ( read != null )
					setImage( read );
				setStatus( null );
			} catch ( Throwable error ) {
				String message = error.getMessage();
				setStatus( message == null ? "Reference import failed." : message );
				failed( error );
			} finally {
				busy = false;
				repaintOnEdt();
			}
		}, "reference-style-encoder" );
		worker.setDaemon( true );
		worker.start();
	}

	protected void setImage( BufferedImage read ) {
		if (read != null )
			runOnEdt( () -> {
				dropped = Imagez.scaleLongest( read, 80 );
				repaint();
			} );
	}

	public void clearImage() {
		runOnEdt( () -> {
			dropped = null;
			status = null;
			repaint();
		} );
	}

	protected void setStatus( String message ) {
		runOnEdt( () -> {
			status = message;
			repaint();
		} );
	}

	protected void failed( Throwable error ) {
		error.printStackTrace();
	}

	private void repaintOnEdt() {
		runOnEdt( this::repaint );
	}

	private static void runOnEdt( Runnable runnable ) {
		if ( SwingUtilities.isEventDispatchThread() )
			runnable.run();
		else
			SwingUtilities.invokeLater( runnable );
	}
	
	@Override
	protected void paintComponent( Graphics g ) {
		super.paintComponent( g );
		
		g.setColor( hover ? Color.darkGray : Color.black );
		g.fillRect( 0, 0, getWidth(), getHeight() );
		
		if (dropped != null) {
			g.drawImage( dropped, (int) ( (getWidth() - getHeight() ) / 2 ), 0, null );
		}
		
		String message = status;
		if ( message == null && hover )
			message = "Drop reference (right click for manual)";
		else if ( message == null && dropped == null )
			message = prompt;
		if ( message != null ) {
			empty.setText( message );
			empty.setSize( getSize() );
			empty.paint( g );
		}
	}
	
	public void rightClick() {}
	
	public BufferedImage process (File f) throws Throwable {
		return null;
	}
}
